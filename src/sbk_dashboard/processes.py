"""Lifecycle-safe ownership and supervision of native monitoring processes."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Protocol, cast

import psutil

from sbk_dashboard.files import atomic_json

STOP_TIMEOUT_SECONDS = 5
GUARDIAN_START_TIMEOUT_SECONDS = 5
LOG_RETRY_INITIAL_SECONDS = 1.0
LOG_RETRY_MAXIMUM_SECONDS = 300.0
MAX_LOCAL_PROBE_ADDRESSES = 256
AUTO_PORT_SEARCH_ATTEMPTS = 1000
LOGGER = logging.getLogger(__name__)

SocketAddress = tuple[str, int] | tuple[str, int, int, int]


@dataclass(frozen=True)
class _SocketEndpoint:
    family: int
    address: SocketAddress


class LifecycleState(Enum):
    """Explicit lifecycle states shared by the stack and owned services."""

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class LifecycleController:
    """Thread-safe State-pattern implementation with validated transitions."""

    _ALLOWED = {
        LifecycleState.NEW: {LifecycleState.STARTING, LifecycleState.STOPPED},
        LifecycleState.STARTING: {LifecycleState.RUNNING, LifecycleState.FAILED, LifecycleState.STOPPING},
        LifecycleState.RUNNING: {LifecycleState.STARTING, LifecycleState.FAILED, LifecycleState.STOPPING},
        LifecycleState.FAILED: {LifecycleState.STARTING, LifecycleState.STOPPING, LifecycleState.STOPPED},
        LifecycleState.STOPPING: {LifecycleState.STOPPED},
        LifecycleState.STOPPED: {LifecycleState.STARTING},
    }

    def __init__(self) -> None:
        self._state = LifecycleState.NEW
        self._lock = threading.Lock()

    @property
    def state(self) -> LifecycleState:
        with self._lock:
            return self._state

    def transition(self, state: LifecycleState) -> None:
        with self._lock:
            if state == self._state:
                return
            if state not in self._ALLOWED[self._state]:
                raise RuntimeError(f"Invalid lifecycle transition: {self._state.value} -> {state.value}")
            self._state = state


class HealthProbe(Protocol):
    """Strategy interface for service readiness and liveness probes."""

    def ready(self) -> bool:
        """Return whether the service endpoint is currently healthy."""


class HttpHealthProbe:
    def __init__(self, url: str, timeout_seconds: float = 2.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout_seconds) as response:
                return 200 <= response.status < 300
        except (OSError, urllib.error.URLError):
            return False


@dataclass(frozen=True)
class RestartPolicy:
    """Bounded exponential-backoff and unhealthy-threshold policy."""

    unhealthy_threshold: int = 3
    initial_backoff_seconds: float = 1.0
    maximum_backoff_seconds: float = 60.0


@dataclass(frozen=True)
class NativeServiceSpec:
    """Immutable Command-pattern description of one native service."""

    name: str
    component: str
    port: int
    command: Callable[[], list[str]]
    health_probe: HealthProbe
    log_path: Path
    log_size_bytes: int
    log_backups: int
    startup_timeout_seconds: int = 45
    bind_address: str = "0.0.0.0"


class ManagedProcessRegistry:
    """Persist enough identity to prevent PID-reuse ownership mistakes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict[str, object]]:
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def record(self, component: str, process: subprocess.Popen[bytes], port: int) -> None:
        self.record_pid(component, process.pid, port)

    def record_pid(self, component: str, pid: int, port: int) -> None:
        try:
            native = psutil.Process(pid)
            started = native.create_time()
            command = native.exe()
        except psutil.Error as error:
            raise OSError(f"Unable to record managed {component} process {pid}: {error}") from error
        with self._lock:
            values = self._read()
            values[component] = {"pid": pid, "started": started, "command": command, "port": port}
            atomic_json(self.path, values)

    def find(self, component: str, port: int) -> psutil.Process | None:
        with self._lock:
            entry = self._read().get(component)
        if not entry or entry.get("port") != port:
            return None
        try:
            raw_pid = entry["pid"]
            raw_started = entry["started"]
            if not isinstance(raw_pid, (str, int)) or not isinstance(raw_started, (str, int, float)):
                return None
            process = psutil.Process(int(raw_pid))
            if abs(process.create_time() - float(raw_started)) > 0.01:
                return None
            if process.exe() != entry.get("command"):
                return None
            return process
        except (psutil.Error, KeyError, TypeError, ValueError):
            return None

    def remove(self, component: str, pid: int) -> None:
        with self._lock:
            values = self._read()
            if values.get(component, {}).get("pid") == pid:
                del values[component]
                atomic_json(self.path, values)


class PortProcessManager:
    """Validate all listener owners before safely replacing any native service."""

    @classmethod
    def terminate_existing(
        cls,
        prometheus_port: int,
        grafana_port: int,
        registry: ManagedProcessRegistry,
        prometheus_bind_address: str = "0.0.0.0",
        grafana_bind_address: str = "0.0.0.0",
        replace_prometheus: bool = True,
        replace_grafana: bool = True,
    ) -> None:
        candidates: list[tuple[str, int, str, list[psutil.Process]]] = []
        cls._inspect(
            "Prometheus", "prometheus", prometheus_port, prometheus_bind_address,
            {"prometheus"}, registry, candidates, replace_prometheus,
        )
        cls._inspect(
            "Grafana", "grafana", grafana_port, grafana_bind_address,
            {"grafana", "grafana-server"}, registry, candidates, replace_grafana,
        )
        stopped: set[int] = set()
        for name, port, _bind_address, processes in candidates:
            for process in processes:
                if process.pid in stopped:
                    continue
                stopped.add(process.pid)
                LOGGER.info("Stopping existing %s process on port %s (pid %s)", name, port, process.pid)
                _terminate_psutil_tree(process, name)
        for name, port, bind_address, _ in candidates:
            deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
            while time.monotonic() < deadline and not cls.available(port, bind_address):
                time.sleep(0.1)
            if not cls.available(port, bind_address):
                raise OSError(f"{name} port {port} remains occupied after stopping its existing process")

    @classmethod
    def _inspect(
        cls,
        name: str,
        component: str,
        port: int,
        bind_address: str,
        valid_names: set[str],
        registry: ManagedProcessRegistry,
        candidates: list[tuple[str, int, str, list[psutil.Process]]],
        allow_replacement: bool = True,
    ) -> None:
        try:
            connections = [
                connection
                for connection in psutil.net_connections(kind="tcp")
                if connection.status == psutil.CONN_LISTEN and getattr(connection.laddr, "port", None) == port
            ]
        except (psutil.Error, OSError) as error:
            if cls.available(port, bind_address):
                return
            owned = registry.find(component, port)
            if owned is None:
                if not allow_replacement:
                    raise OSError(
                        cls._configured_port_occupied_message(name, port, bind_address, [])
                    ) from error
                raise OSError(
                    f"Port {port} is occupied, but listener discovery is unavailable; no process was stopped"
                ) from error
            processes = [owned]
        else:
            if not connections:
                if not allow_replacement and not cls.available(port, bind_address):
                    raise OSError(cls._configured_port_occupied_message(name, port, bind_address, []))
                return
            pids = {connection.pid for connection in connections if connection.pid}
            if not pids:
                owned = registry.find(component, port)
                if owned is None:
                    if not allow_replacement:
                        raise OSError(
                            cls._configured_port_occupied_message(name, port, bind_address, [])
                        )
                    raise OSError(
                        f"Port {port} is occupied, but its owner cannot be identified safely; no process was stopped"
                    )
                processes = [owned]
            else:
                try:
                    processes = [psutil.Process(pid) for pid in pids]
                except psutil.Error as error:
                    raise OSError(f"Listener process disappeared from port {port}; no process was stopped") from error
        if not allow_replacement:
            raise OSError(cls._configured_port_occupied_message(name, port, bind_address, processes))
        for process in processes:
            try:
                command = process.exe()
            except psutil.Error:
                try:
                    command = process.name()
                except psutil.Error:
                    command = ""
            base = Path(command).name.lower().removesuffix(".exe")
            if base not in valid_names:
                description = command or "unknown command"
                raise OSError(
                    f"Port {port} is owned by unrelated process {process.pid} ({description}); no process was stopped"
                )
        candidates.append((name, port, bind_address, processes))

    @classmethod
    def require_available(
        cls,
        name: str,
        port: int,
        bind_address: str,
        source: str,
    ) -> None:
        """Reject an occupied operator-selected port without stopping its owner."""
        if cls.available(port, bind_address):
            return
        processes: list[psutil.Process] = []
        try:
            connections = [
                connection
                for connection in psutil.net_connections(kind="tcp")
                if connection.status == psutil.CONN_LISTEN
                and getattr(connection.laddr, "port", None) == port
            ]
            processes = [psutil.Process(pid) for pid in sorted({item.pid for item in connections if item.pid})]
        except (psutil.Error, OSError):
            processes = []
        detail = cls._occupied_port_message(name, port, bind_address, processes)
        raise OSError(
            f"{detail}; this port was user supplied via {source}. Choose an available {name} port; "
            "no process was stopped"
        )

    @staticmethod
    def _occupied_port_message(
        name: str,
        port: int,
        bind_address: str,
        processes: list[psutil.Process],
    ) -> str:
        owners: list[str] = []
        for process in processes:
            try:
                command = process.exe()
            except psutil.Error:
                try:
                    command = process.name()
                except psutil.Error:
                    command = "unknown command"
            owners.append(f"PID {process.pid} ({command or 'unknown command'})")
        owner_detail = ", ".join(owners) if owners else "an unidentified listener"
        return f"{name} port {port} on bind address {bind_address} is already in use by {owner_detail}"

    @classmethod
    def _configured_port_occupied_message(
        cls,
        name: str,
        port: int,
        bind_address: str,
        processes: list[psutil.Process],
    ) -> str:
        return (
            f"{cls._occupied_port_message(name, port, bind_address, processes)}; "
            f"the configured {name} port is user supplied and cannot be replaced. "
            f"Choose an available {name} port; no process was stopped"
        )

    @staticmethod
    def available(port: int, bind_address: str = "0.0.0.0") -> bool:
        try:
            endpoints = PortProcessManager._resolve_endpoints(bind_address, port)
            for endpoint in endpoints:
                for connect_address in PortProcessManager._connect_addresses(endpoint, bind_address):
                    with socket.socket(endpoint.family, socket.SOCK_STREAM) as connection:
                        connection.settimeout(0.2)
                        if connection.connect_ex(connect_address) == 0:
                            return False
            for endpoint in endpoints:
                if PortProcessManager._can_listen(endpoint):
                    continue
                if not (
                    os.name == "nt"
                    and PortProcessManager._windows_time_wait_only(port, endpoint.family)
                    and PortProcessManager._can_listen(endpoint, reuse=True)
                ):
                    return False
            return True
        except OSError:
            return False

    @classmethod
    def find_available(
        cls,
        preferred_port: int,
        bind_address: str,
        excluded: set[int] | None = None,
    ) -> int:
        """Return the preferred port or a bounded deterministic fallback."""
        unavailable = set() if excluded is None else set(excluded)
        if preferred_port not in unavailable and cls.available(preferred_port, bind_address):
            return preferred_port
        candidate = preferred_port
        for _ in range(AUTO_PORT_SEARCH_ATTEMPTS):
            candidate = 1024 if candidate >= 65535 else candidate + 1
            if candidate in unavailable:
                continue
            if cls.available(candidate, bind_address):
                return candidate
        raise OSError(
            f"No available port was found for {bind_address} after "
            f"{AUTO_PORT_SEARCH_ATTEMPTS} attempts following port {preferred_port}"
        )

    @staticmethod
    def _resolve_endpoints(bind_address: str, port: int) -> tuple[_SocketEndpoint, ...]:
        try:
            address = ipaddress.ip_address(bind_address)
        except ValueError:
            results = socket.getaddrinfo(
                bind_address, port, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP
            )
            endpoints: list[_SocketEndpoint] = []
            seen: set[tuple[int, SocketAddress]] = set()
            for family, sock_type, _protocol, _canonical_name, raw_address in results:
                if family not in {socket.AF_INET, socket.AF_INET6} or sock_type != socket.SOCK_STREAM:
                    continue
                endpoint_address = cast(SocketAddress, raw_address)
                key = (family, endpoint_address)
                if key not in seen:
                    seen.add(key)
                    endpoints.append(_SocketEndpoint(family, endpoint_address))
            if not endpoints:
                raise OSError(
                    f"Bind address {bind_address!r} did not resolve to an IPv4 or IPv6 address"
                ) from None
            return tuple(endpoints)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        literal_endpoint: SocketAddress = (
            (bind_address, port, 0, 0) if family == socket.AF_INET6 else (bind_address, port)
        )
        return (_SocketEndpoint(family, literal_endpoint),)

    @staticmethod
    def _connect_addresses(endpoint: _SocketEndpoint, bind_address: str) -> tuple[SocketAddress, ...]:
        if bind_address not in {"0.0.0.0", "::"}:
            return (endpoint.address,)
        loopback: SocketAddress = (
            ("::1", endpoint.address[1], 0, 0)
            if endpoint.family == socket.AF_INET6
            else ("127.0.0.1", endpoint.address[1])
        )
        addresses = [loopback]
        seen = {loopback[0]}
        try:
            interfaces = psutil.net_if_addrs()
        except psutil.Error:
            interfaces = {}
        for interface_addresses in interfaces.values():
            for address in interface_addresses:
                if address.family != endpoint.family:
                    continue
                raw_address = address.address.split("%", 1)[0]
                try:
                    parsed_address = ipaddress.ip_address(raw_address)
                except ValueError:
                    continue
                if parsed_address.is_link_local:
                    continue
                normalized_address = str(parsed_address)
                if normalized_address in seen:
                    continue
                seen.add(normalized_address)
                socket_address: SocketAddress = (
                    (normalized_address, endpoint.address[1], 0, 0)
                    if endpoint.family == socket.AF_INET6
                    else (normalized_address, endpoint.address[1])
                )
                addresses.append(socket_address)
                if len(addresses) >= MAX_LOCAL_PROBE_ADDRESSES:
                    return tuple(addresses)
        return tuple(addresses)

    @staticmethod
    def _can_listen(endpoint: _SocketEndpoint, reuse: bool = False) -> bool:
        try:
            with socket.socket(endpoint.family, socket.SOCK_STREAM) as probe:
                if os.name == "nt" and not reuse:
                    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                    if exclusive is None:
                        return False
                    probe.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                else:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(endpoint.address)
                probe.listen(1)
            return True
        except OSError:
            return False

    @staticmethod
    def _windows_time_wait_only(port: int, family: int) -> bool:
        try:
            connections = psutil.net_connections(kind="tcp")
        except psutil.Error:
            return False
        matches = [
            connection
            for connection in connections
            if connection.family == family
            and connection.laddr
            and connection.laddr.port == port
        ]
        return bool(matches) and all(connection.status == psutil.CONN_TIME_WAIT for connection in matches)


class RotatingProcessLog:
    """Bounded-memory pipe drain with bounded on-disk rotation."""

    def __init__(self, process: subprocess.Popen[bytes], path: Path, size_bytes: int, backups: int) -> None:
        self._process = process
        self._path = path
        self._size_bytes = size_bytes
        self._backups = backups
        self._thread = threading.Thread(target=self._run, name=f"{path.stem}-log-pump", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._thread.join(timeout=2)
        close_error: OSError | ValueError | None = None
        if self._process.stdout and not self._process.stdout.closed:
            try:
                self._process.stdout.close()
            except (OSError, ValueError) as error:
                close_error = error
        if self._thread.is_alive():
            self._thread.join(timeout=1)
        if self._thread.is_alive():
            raise OSError(f"Native process log pump for {self._path} did not stop within 3 seconds") from close_error
        if close_error:
            raise OSError(f"Unable to close native process output pipe for {self._path}") from close_error

    def _open_output(self) -> tuple[BinaryIO, int]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        output = self._path.open("ab", buffering=0)
        try:
            return output, self._path.stat().st_size
        except (OSError, ValueError):
            with suppress(OSError, ValueError):
                output.close()
            raise

    def _run(self) -> None:
        source = self._process.stdout
        if source is None:
            return
        output = None
        size = 0
        retry_delay = LOG_RETRY_INITIAL_SECONDS
        next_retry = 0.0
        logging_failed = False
        try:
            output, size = self._open_output()
        except (OSError, ValueError) as error:
            logging_failed = True
            next_retry = time.monotonic() + retry_delay
            retry_delay = min(LOG_RETRY_MAXIMUM_SECONDS, retry_delay * 2)
            LOGGER.warning("Native process logging unavailable for %s; retrying with backoff: %s", self._path, error)
        try:
            while True:
                chunk = source.read(64 * 1024)
                if not chunk:
                    return
                if output is None:
                    now = time.monotonic()
                    if now < next_retry:
                        continue
                    try:
                        output, size = self._open_output()
                    except (OSError, ValueError) as error:
                        LOGGER.debug("Native process logging retry failed for %s: %s", self._path, error)
                        next_retry = now + retry_delay
                        retry_delay = min(LOG_RETRY_MAXIMUM_SECONDS, retry_delay * 2)
                        continue
                    if logging_failed:
                        LOGGER.info("Native process logging recovered for %s", self._path)
                    logging_failed = False
                    retry_delay = LOG_RETRY_INITIAL_SECONDS
                offset = 0
                try:
                    while offset < len(chunk):
                        if size >= self._size_bytes:
                            output.close()
                            self._rotate()
                            output = self._path.open("ab", buffering=0)
                            size = 0
                        count = min(self._size_bytes - size, len(chunk) - offset)
                        written = 0
                        while written < count:
                            result = output.write(chunk[offset + written : offset + count])
                            if not isinstance(result, int) or result <= 0:
                                raise OSError("Native process log write made no progress")
                            written += result
                        size += count
                        offset += count
                except (OSError, ValueError) as error:
                    with suppress(OSError, ValueError):
                        output.close()
                    output = None
                    logging_failed = True
                    next_retry = time.monotonic() + retry_delay
                    retry_delay = min(LOG_RETRY_MAXIMUM_SECONDS, retry_delay * 2)
                    LOGGER.warning(
                        "Native process logging failed for %s; retrying with backoff: %s", self._path, error
                    )
        except (OSError, ValueError) as error:
            LOGGER.warning("Native process output drain failed for %s: %s", self._path, error)
        finally:
            if output is not None:
                with suppress(OSError, ValueError):
                    output.close()

    def _rotate(self) -> None:
        if self._backups < 1:
            self._path.unlink(missing_ok=True)
            return
        oldest = self._path.with_name(f"{self._path.name}.{self._backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self._backups - 1, 0, -1):
            source = self._path.with_name(f"{self._path.name}.{index}")
            if source.exists():
                os.replace(source, self._path.with_name(f"{self._path.name}.{index + 1}"))
        if self._path.exists():
            os.replace(self._path, self._path.with_name(f"{self._path.name}.1"))


class ManagedNativeService:
    """Facade owning one native service across start, supervise, restart, and stop."""

    def __init__(
        self,
        spec: NativeServiceSpec,
        registry: ManagedProcessRegistry,
        shutdown_event: threading.Event,
        restart_policy: RestartPolicy | None = None,
    ) -> None:
        self.spec = spec
        self.registry = registry
        self.shutdown_event = shutdown_event
        self.restart_policy = restart_policy or RestartPolicy()
        self.lifecycle = LifecycleController()
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._native_pid: int | None = None
        self._native_started: float | None = None
        self._log_pump: RotatingProcessLog | None = None
        self._attached = False
        self._last_healthy = False
        self._unhealthy_count = 0
        self._restart_failures = 0
        self._next_restart = 0.0

    @property
    def attached(self) -> bool:
        with self._state_lock:
            return self._attached

    @property
    def pid(self) -> int | None:
        with self._state_lock:
            return self._native_pid

    def start(self, continue_existing: bool) -> None:
        with self._operation_lock:
            self.lifecycle.transition(LifecycleState.STARTING)
            try:
                if continue_existing and self.spec.health_probe.ready():
                    with self._state_lock:
                        self._attached = True
                        self._last_healthy = True
                    LOGGER.info("Continuing existing %s on port %s", self.spec.name, self.spec.port)
                else:
                    if continue_existing and not PortProcessManager.available(
                        self.spec.port, self.spec.bind_address
                    ):
                        raise OSError(
                            f"Port {self.spec.port} is occupied but does not expose a healthy {self.spec.name} "
                            "service; -continue true cannot attach to it"
                        )
                    self._launch()
                self.lifecycle.transition(LifecycleState.RUNNING)
            except BaseException:
                self.lifecycle.transition(LifecycleState.FAILED)
                self._stop_process()
                raise

    def healthy(self) -> bool:
        if self.lifecycle.state != LifecycleState.RUNNING:
            return False
        with self._state_lock:
            attached = self._attached
            process = self._process
            native_pid = self._native_pid
            native_started = self._native_started
            last_healthy = self._last_healthy
        return last_healthy and (
            attached
            or (
                process is not None
                and process.poll() is None
                and self._native_alive(native_pid, native_started)
            )
        )

    def supervise(self) -> bool:
        if self.shutdown_event.is_set() or self.lifecycle.state not in {
            LifecycleState.RUNNING,
            LifecycleState.FAILED,
        }:
            return False
        with self._state_lock:
            attached = self._attached
            process = self._process
            native_pid = self._native_pid
            native_started = self._native_started
        if attached:
            ready = self.spec.health_probe.ready()
            with self._state_lock:
                self._last_healthy = ready
            return ready
        ready = self.spec.health_probe.ready()
        native_alive = self._native_alive(native_pid, native_started)
        if process is not None and process.poll() is None and native_alive and ready:
            with self._state_lock:
                self._last_healthy = True
            self._unhealthy_count = 0
            self._restart_failures = 0
            return True
        self._unhealthy_count += 1
        with self._state_lock:
            self._last_healthy = False
        if (
            process is not None
            and process.poll() is None
            and native_alive
            and self._unhealthy_count < self.restart_policy.unhealthy_threshold
        ):
            return False
        if time.monotonic() < self._next_restart:
            return False
        return self._restart()

    def stop(self) -> None:
        with self._operation_lock:
            state = self.lifecycle.state
            if state == LifecycleState.STOPPED:
                return
            if state == LifecycleState.NEW:
                self.lifecycle.transition(LifecycleState.STOPPED)
                return
            if state != LifecycleState.STOPPING:
                self.lifecycle.transition(LifecycleState.STOPPING)
            self._stop_process()
            with self._state_lock:
                self._attached = False
                self._last_healthy = False
            self.lifecycle.transition(LifecycleState.STOPPED)

    def _restart(self) -> bool:
        if not self._operation_lock.acquire(blocking=False):
            return False
        try:
            if self.shutdown_event.is_set():
                return False
            LOGGER.warning("Restarting unhealthy managed %s", self.spec.name)
            state = self.lifecycle.state
            if state in {LifecycleState.RUNNING, LifecycleState.FAILED}:
                self.lifecycle.transition(LifecycleState.STARTING)
            try:
                self._stop_process()
                self._launch()
                self._unhealthy_count = 0
                self._restart_failures = 0
                with self._state_lock:
                    self._last_healthy = True
                self.lifecycle.transition(LifecycleState.RUNNING)
                return True
            except (OSError, subprocess.SubprocessError) as error:
                self._restart_failures += 1
                backoff = min(
                    self.restart_policy.maximum_backoff_seconds,
                    self.restart_policy.initial_backoff_seconds * (2 ** min(self._restart_failures - 1, 16)),
                )
                self._next_restart = time.monotonic() + backoff
                self.lifecycle.transition(LifecycleState.FAILED)
                LOGGER.warning(
                    "Unable to restart %s; retrying in %gs: %s", self.spec.name, backoff, error
                )
                return False
        finally:
            self._operation_lock.release()

    def _launch(self) -> None:
        command = self.spec.command()
        self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        guardian_state = self.spec.log_path.with_name(
            f".{self.spec.component}-guardian-{os.getpid()}.json"
        )
        guardian_state.unlink(missing_ok=True)
        parent_started = psutil.Process(os.getpid()).create_time()
        guardian_entry = (
            [sys.executable, "--internal-guardian"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "sbk_dashboard.guardian"]
        )
        guardian_command = [
            *guardian_entry,
            "--parent-pid",
            str(os.getpid()),
            "--parent-started",
            repr(parent_started),
            "--pid-file",
            str(guardian_state),
            "--name",
            self.spec.name,
            "--",
            *command,
        ]
        if os.name == "nt":
            process = subprocess.Popen(
                guardian_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        else:
            process = subprocess.Popen(
                guardian_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=0,
                start_new_session=True,
            )
        pump = RotatingProcessLog(process, self.spec.log_path, self.spec.log_size_bytes, self.spec.log_backups)
        pump.start()
        with self._state_lock:
            self._process = process
            self._log_pump = pump
        try:
            native_pid, native_started = self._await_guardian_start(process, guardian_state)
            with self._state_lock:
                self._native_pid = native_pid
                self._native_started = native_started
            self.registry.record_pid(self.spec.component, native_pid, self.spec.port)
            LOGGER.info(
                "Started managed process %s (pid %s, guardian pid %s)",
                command[0],
                native_pid,
                process.pid,
            )
            self._await_ready(process)
        except BaseException:
            self._stop_process()
            raise
        finally:
            guardian_state.unlink(missing_ok=True)

    def _await_guardian_start(
        self, process: subprocess.Popen[bytes], state_path: Path
    ) -> tuple[int, float]:
        timeout = min(GUARDIAN_START_TIMEOUT_SECONDS, self.spec.startup_timeout_seconds)
        deadline = time.monotonic() + timeout
        last_transient_error: PermissionError | None = None
        while time.monotonic() < deadline:
            if self.shutdown_event.is_set():
                raise OSError(f"{self.spec.name} guardian startup cancelled during shutdown")
            code = process.poll()
            if code is not None:
                raise OSError(f"{self.spec.name} guardian exited during startup with code {code}")
            try:
                serialized = state_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                pass
            except PermissionError as error:
                # Windows may briefly deny access after the guardian atomically replaces the handshake file.
                last_transient_error = error
            else:
                try:
                    value = json.loads(serialized)
                    raw_pid = value.get("pid")
                    if isinstance(raw_pid, bool) or not isinstance(raw_pid, int) or raw_pid < 1:
                        raise ValueError("guardian PID is invalid")
                    native = psutil.Process(raw_pid)
                    if native.ppid() != process.pid:
                        raise ValueError("guardian child relationship is invalid")
                    return raw_pid, native.create_time()
                except (OSError, ValueError, TypeError, psutil.Error) as error:
                    raise OSError(f"{self.spec.name} guardian state is invalid: {error}") from error
            self.shutdown_event.wait(0.05)
        detail = f"; last state read failed: {last_transient_error}" if last_transient_error else ""
        raise OSError(f"{self.spec.name} guardian did not launch its native process within {timeout} seconds{detail}")

    @staticmethod
    def _native_alive(pid: int | None, started: float | None) -> bool:
        if pid is None or started is None:
            return False
        try:
            native = psutil.Process(pid)
            return (
                native.is_running()
                and native.status() != psutil.STATUS_ZOMBIE
                and abs(native.create_time() - started) <= 0.01
            )
        except psutil.Error:
            return False

    def _await_ready(self, process: subprocess.Popen[bytes]) -> None:
        deadline = time.monotonic() + self.spec.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.shutdown_event.is_set():
                raise OSError(f"{self.spec.name} startup cancelled during shutdown")
            code = process.poll()
            if code is not None:
                raise OSError(
                    f"{self.spec.name} exited during startup with code {code}; "
                    f"check whether port {self.spec.port} is already in use"
                )
            if self.spec.health_probe.ready():
                if self.shutdown_event.wait(0.5):
                    raise OSError(f"{self.spec.name} startup cancelled during shutdown")
                if process.poll() is not None:
                    raise OSError(f"{self.spec.name} exited during startup with code {process.returncode}")
                with self._state_lock:
                    self._last_healthy = True
                LOGGER.info("%s ready on port %s", self.spec.name, self.spec.port)
                return
            self.shutdown_event.wait(0.25)
        raise OSError(
            f"{self.spec.name} did not become ready within {self.spec.startup_timeout_seconds} seconds"
        )

    def _stop_process(self) -> None:
        with self._state_lock:
            process = self._process
            native_pid = self._native_pid
            native_started = self._native_started
            pump = self._log_pump
            self._process = None
            self._native_pid = None
            self._native_started = None
            self._log_pump = None
        if process is None:
            return
        termination_error: OSError | None = None
        pump_error: OSError | None = None
        try:
            try:
                _terminate_owned_process(process, self.spec.name)
            except OSError as error:
                termination_error = error
            if self._native_alive(native_pid, native_started):
                try:
                    _terminate_psutil_tree(psutil.Process(native_pid), self.spec.name)
                except (OSError, psutil.Error) as error:
                    if termination_error is None:
                        termination_error = OSError(
                            f"Unable to stop guarded native {self.spec.name} process {native_pid}: {error}"
                        )
                    else:
                        termination_error = OSError(f"{termination_error}; guarded native cleanup: {error}")
        finally:
            if pump:
                try:
                    pump.close()
                except OSError as error:
                    pump_error = error
            try:
                if native_pid is not None:
                    self.registry.remove(self.spec.component, native_pid)
            except OSError as error:
                LOGGER.warning("Unable to update managed process ownership: %s", error)
        if termination_error and pump_error:
            raise OSError(f"{termination_error}; additionally, {pump_error}") from termination_error
        if termination_error:
            raise termination_error
        if pump_error:
            raise pump_error


def _terminate_owned_process(process: subprocess.Popen[bytes], name: str) -> None:
    descendants: list[psutil.Process] = []
    with suppress(psutil.Error):
        descendants = psutil.Process(process.pid).children(recursive=True)
    if os.name != "nt":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    else:
        for child in descendants:
            with suppress(psutil.Error):
                child.terminate()
        process.terminate()
    try:
        process.wait(STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        LOGGER.warning(
            "%s pid %s did not stop gracefully; forcing process-group termination", name, process.pid
        )
        if os.name != "nt":
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            with suppress(psutil.Error):
                current_descendants = psutil.Process(process.pid).children(recursive=True)
                descendants = _unique_processes([*descendants, *current_descendants])
            for child in reversed(descendants):
                with suppress(psutil.Error):
                    child.kill()
            process.kill()
        try:
            process.wait(STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise OSError(f"Unable to stop managed {name} process {process.pid}") from error
    _finish_descendants(descendants)
    if os.name != "nt":
        # The guardian is the process-group leader. Captured descendants received their bounded graceful
        # wait above; now kill any late group member that was created after the initial tree snapshot.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def _terminate_psutil_tree(process: psutil.Process, name: str) -> None:
    try:
        descendants = process.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    for child in reversed(descendants):
        with suppress(psutil.NoSuchProcess):
            child.terminate()
    candidates = [*descendants, process]
    try:
        process.terminate()
    except psutil.NoSuchProcess:
        candidates = descendants
    if not candidates:
        return
    _, alive = psutil.wait_procs(candidates, timeout=STOP_TIMEOUT_SECONDS)
    if alive:
        LOGGER.warning("%s pid %s did not stop gracefully; forcing termination", name, process.pid)
        with suppress(psutil.Error):
            alive = _unique_processes([*alive, *process.children(recursive=True)])
        for item in alive:
            with suppress(psutil.NoSuchProcess):
                item.kill()
        _, alive = psutil.wait_procs(alive, timeout=STOP_TIMEOUT_SECONDS)
    if alive:
        raise OSError(f"Unable to stop existing {name} process {process.pid}")


def _unique_processes(processes: list[psutil.Process]) -> list[psutil.Process]:
    unique: dict[int, psutil.Process] = {}
    for process in processes:
        with suppress(psutil.Error):
            unique[process.pid] = process
    return list(unique.values())


def _finish_descendants(descendants: list[psutil.Process]) -> None:
    if not descendants:
        return
    _, alive = psutil.wait_procs(descendants, timeout=1)
    for process in alive:
        with suppress(psutil.Error):
            process.kill()
    _, alive = psutil.wait_procs(alive, timeout=1)
    if alive:
        identifiers = ", ".join(str(process.pid) for process in alive)
        raise OSError(f"Unable to stop managed descendant process(es): {identifiers}")

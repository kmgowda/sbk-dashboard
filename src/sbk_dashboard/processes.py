"""Lifecycle-safe ownership and supervision of native monitoring processes."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import psutil

from sbk_dashboard.files import atomic_json

STOP_TIMEOUT_SECONDS = 5
LOGGER = logging.getLogger(__name__)


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
        try:
            native = psutil.Process(process.pid)
            started = native.create_time()
            command = native.exe()
        except psutil.Error as error:
            raise OSError(f"Unable to record managed {component} process {process.pid}: {error}") from error
        with self._lock:
            values = self._read()
            values[component] = {"pid": process.pid, "started": started, "command": command, "port": port}
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
    ) -> None:
        candidates: list[tuple[str, int, str, list[psutil.Process]]] = []
        cls._inspect(
            "Prometheus", "prometheus", prometheus_port, prometheus_bind_address,
            {"prometheus"}, registry, candidates,
        )
        cls._inspect(
            "Grafana", "grafana", grafana_port, grafana_bind_address,
            {"grafana", "grafana-server"}, registry, candidates,
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
                raise OSError(
                    f"Port {port} is occupied, but listener discovery is unavailable; no process was stopped"
                ) from error
            processes = [owned]
        else:
            if not connections:
                return
            pids = {connection.pid for connection in connections if connection.pid}
            if not pids:
                owned = registry.find(component, port)
                if owned is None:
                    raise OSError(
                        f"Port {port} is occupied, but its owner cannot be identified safely; no process was stopped"
                    )
                processes = [owned]
            else:
                try:
                    processes = [psutil.Process(pid) for pid in pids]
                except psutil.Error as error:
                    raise OSError(f"Listener process disappeared from port {port}; no process was stopped") from error
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

    @staticmethod
    def available(port: int, bind_address: str = "0.0.0.0") -> bool:
        try:
            if any(
                connection.status == psutil.CONN_LISTEN and getattr(connection.laddr, "port", None) == port
                for connection in psutil.net_connections(kind="tcp")
            ):
                return False
        except (psutil.Error, OSError):
            pass
        try:
            try:
                family = socket.AF_INET6 if ipaddress.ip_address(bind_address).version == 6 else socket.AF_INET
            except ValueError:
                family = socket.AF_INET6 if ":" in bind_address else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                if os.name == "nt":
                    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                    if exclusive is None:
                        return False
                    probe.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                else:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((bind_address, port))
            return True
        except OSError:
            return False


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
        if self._process.stdout and not self._process.stdout.closed:
            self._process.stdout.close()
        if self._thread.is_alive():
            self._thread.join(timeout=1)

    def _run(self) -> None:
        source = self._process.stdout
        if source is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        output = None
        try:
            output = self._path.open("ab", buffering=0)
            size = self._path.stat().st_size
            while True:
                chunk = source.read(64 * 1024)
                if not chunk:
                    return
                offset = 0
                while offset < len(chunk):
                    if size >= self._size_bytes:
                        output.close()
                        self._rotate()
                        output = self._path.open("ab", buffering=0)
                        size = 0
                    count = min(self._size_bytes - size, len(chunk) - offset)
                    output.write(chunk[offset : offset + count])
                    size += count
                    offset += count
        except (OSError, ValueError) as error:
            LOGGER.warning("Native process log pump failed for %s: %s", self._path, error)
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
            return self._process.pid if self._process else None

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
            last_healthy = self._last_healthy
        return last_healthy and (attached or (process is not None and process.poll() is None))

    def supervise(self) -> bool:
        if self.shutdown_event.is_set() or self.lifecycle.state not in {
            LifecycleState.RUNNING,
            LifecycleState.FAILED,
        }:
            return False
        with self._state_lock:
            attached = self._attached
            process = self._process
        if attached:
            ready = self.spec.health_probe.ready()
            with self._state_lock:
                self._last_healthy = ready
            return ready
        ready = self.spec.health_probe.ready()
        if process is not None and process.poll() is None and ready:
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
        if os.name == "nt":
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        else:
            process = subprocess.Popen(
                command,
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
        LOGGER.info("Started managed process %s (pid %s)", command[0], process.pid)
        try:
            self.registry.record(self.spec.component, process, self.spec.port)
            self._await_ready(process)
        except BaseException:
            self._stop_process()
            raise

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
            pump = self._log_pump
            self._process = None
            self._log_pump = None
        if process is None:
            return
        termination_error: OSError | None = None
        try:
            _terminate_owned_process(process, self.spec.name)
        except OSError as error:
            termination_error = error
        finally:
            if pump:
                pump.close()
            try:
                self.registry.remove(self.spec.component, process.pid)
            except OSError as error:
                LOGGER.warning("Unable to update managed process ownership: %s", error)
        if termination_error:
            raise termination_error


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
            process.kill()
        try:
            process.wait(STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise OSError(f"Unable to stop managed {name} process {process.pid}") from error
    _finish_descendants(descendants)


def _terminate_psutil_tree(process: psutil.Process, name: str) -> None:
    try:
        descendants = process.children(recursive=True)
        for child in reversed(descendants):
            child.terminate()
        process.terminate()
        _, alive = psutil.wait_procs([*descendants, process], timeout=STOP_TIMEOUT_SECONDS)
        if alive:
            LOGGER.warning("%s pid %s did not stop gracefully; forcing termination", name, process.pid)
            for item in alive:
                item.kill()
            _, alive = psutil.wait_procs(alive, timeout=STOP_TIMEOUT_SECONDS)
        if alive:
            raise OSError(f"Unable to stop existing {name} process {process.pid}")
    except psutil.NoSuchProcess:
        return


def _finish_descendants(descendants: list[psutil.Process]) -> None:
    if not descendants:
        return
    _, alive = psutil.wait_procs(descendants, timeout=1)
    for process in alive:
        with suppress(psutil.Error):
            process.kill()
    psutil.wait_procs(alive, timeout=1)

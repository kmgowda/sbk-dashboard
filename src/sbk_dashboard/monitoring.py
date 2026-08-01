"""Native Prometheus and Grafana lifecycle and health management."""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psutil

from sbk_dashboard.config import DashboardConfig, MonitoringConfig, RuntimePlatform, executable, resolve_on_path
from sbk_dashboard.files import atomic_json, atomic_write
from sbk_dashboard.models import BenchmarkTarget, TargetStatus
from sbk_dashboard.provisioning import (
    DATASOURCE_UID,
    GrafanaDashboardProvisioner,
    PrometheusTargetDiscovery,
    write_dashboard_mappings,
)

STARTUP_TIMEOUT_SECONDS = 45
STOP_TIMEOUT_SECONDS = 5


class ManagedProcessRegistry:
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
            process = psutil.Process(int(entry["pid"]))
            if abs(process.create_time() - float(entry["started"])) > 0.01:
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
    @classmethod
    def terminate_existing(cls, prometheus_port: int, grafana_port: int, registry: ManagedProcessRegistry) -> None:
        candidates: list[tuple[str, int, list[psutil.Process]]] = []
        cls._inspect("Prometheus", "prometheus", prometheus_port, {"prometheus"}, registry, candidates)
        cls._inspect("Grafana", "grafana", grafana_port, {"grafana", "grafana-server"}, registry, candidates)
        stopped: set[int] = set()
        for name, port, processes in candidates:
            for process in processes:
                if process.pid in stopped:
                    continue
                stopped.add(process.pid)
                print(f"Stopping existing {name} process on port {port} (pid {process.pid})")
                cls._stop(process, name)
        for name, port, _ in candidates:
            deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
            while time.monotonic() < deadline and not cls.available(port):
                time.sleep(0.1)
            if not cls.available(port):
                raise OSError(f"{name} port {port} remains occupied after stopping its existing process")

    @classmethod
    def _inspect(cls, name: str, component: str, port: int, valid_names: set[str],
                 registry: ManagedProcessRegistry,
                 candidates: list[tuple[str, int, list[psutil.Process]]]) -> None:
        try:
            connections = [connection for connection in psutil.net_connections(kind="tcp")
                           if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port]
        except (psutil.Error, OSError) as error:
            if cls.available(port):
                return
            owned = registry.find(component, port)
            if owned is None:
                raise OSError(f"Port {port} is occupied, but listener discovery is unavailable; no process was "
                              "stopped") from error
            connections = []
            processes = [owned]
        else:
            if not connections:
                return
            pids = {connection.pid for connection in connections if connection.pid}
            if len(pids) < 1:
                owned = registry.find(component, port)
                if owned is None:
                    raise OSError(f"Port {port} is occupied, but its owner cannot be identified safely; no process "
                                  "was stopped")
                processes = [owned]
            else:
                try:
                    processes = [psutil.Process(pid) for pid in pids]
                except psutil.Error as error:
                    raise OSError(f"Listener process disappeared from port {port}; no process was stopped") from error
        for process in processes:
            try:
                command = process.exe()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                command = process.name()
            base = Path(command).name.lower()
            if base.endswith(".exe"):
                base = base[:-4]
            if base not in valid_names:
                description = command or "unknown command"
                raise OSError(
                    f"Port {port} is owned by unrelated process {process.pid} ({description}); no process was stopped"
                )
        candidates.append((name, port, processes))

    @staticmethod
    def _stop(process: psutil.Process, name: str) -> None:
        try:
            process.terminate()
            process.wait(STOP_TIMEOUT_SECONDS)
        except psutil.TimeoutExpired:
            print(f"WARNING: {name} pid {process.pid} did not stop gracefully; forcing termination")
            process.kill()
            try:
                process.wait(STOP_TIMEOUT_SECONDS)
            except psutil.TimeoutExpired as error:
                raise OSError(f"Unable to stop existing {name} process {process.pid}") from error
        except psutil.NoSuchProcess:
            return

    @staticmethod
    def available(port: int) -> bool:
        family = socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                probe.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


class ManagedMonitoringStack:
    """Configure, launch, reconcile, observe, and stop the native monitoring stack."""

    def __init__(self, dashboard: DashboardConfig, monitoring: MonitoringConfig,
                 initial_targets: list[BenchmarkTarget]) -> None:
        self.dashboard = dashboard
        self.monitoring = monitoring
        self.runtime_directory = dashboard.data_directory / "monitoring"
        self.target_discovery = PrometheusTargetDiscovery(self.runtime_directory / "prometheus/targets.json")
        self.dashboard_provisioner = GrafanaDashboardProvisioner(
            self.runtime_directory / "grafana/dashboards", monitoring.grafana_public_url,
        )
        self.process_registry = ManagedProcessRegistry(self.runtime_directory / "managed-processes.json")
        self.statuses: dict[str, TargetStatus] = {}
        self.targets: list[BenchmarkTarget] = []
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self.prometheus_process: subprocess.Popen[bytes] | None = None
        self.grafana_process: subprocess.Popen[bytes] | None = None
        self.prometheus_attached = False
        self.grafana_attached = False
        self._closed = False
        self._prepare_configuration()
        self.reconcile(initial_targets)
        try:
            self._start_components()
        except BaseException:
            self.close()
            raise
        self.refresh_statuses()
        self._monitor_thread = threading.Thread(target=self._monitor_statuses, name="sbk-status-monitor", daemon=True)
        self._monitor_thread.start()

    def _start_components(self) -> None:
        if not self.dashboard.continue_existing:
            PortProcessManager.terminate_existing(
                self.monitoring.prometheus_port, self.monitoring.grafana_port, self.process_registry,
            )
        prometheus_health = self._prometheus_health()
        if self.dashboard.continue_existing and _ready(prometheus_health):
            self.prometheus_attached = True
            print(f"Continuing existing Prometheus on port {self.monitoring.prometheus_port}")
        else:
            self._require_available("Prometheus", self.monitoring.prometheus_port)
            self.prometheus_process = self._start_prometheus()
            self.process_registry.record("prometheus", self.prometheus_process, self.monitoring.prometheus_port)
            self._await_ready("Prometheus", self.prometheus_process, prometheus_health)
        grafana_health = self._grafana_health()
        if self.dashboard.continue_existing and _ready(grafana_health):
            self.grafana_attached = True
            print(f"Continuing existing Grafana on port {self.monitoring.grafana_port}")
        else:
            self._require_available("Grafana", self.monitoring.grafana_port)
            self.grafana_process = self._start_grafana()
            self.process_registry.record("grafana", self.grafana_process, self.monitoring.grafana_port)
            self._await_ready("Grafana", self.grafana_process, grafana_health)

    def reconcile(self, targets: list[BenchmarkTarget]) -> None:
        with self._lock:
            self.targets = list(targets)
            self.target_discovery.write(self.targets)
            self.dashboard_provisioner.reconcile(self.targets)
            write_dashboard_mappings(self.dashboard.data_directory / "dashboard-mappings.json", self.targets,
                                     self.dashboard_provisioner)
            now = _now()
            for target in self.targets:
                self.statuses.setdefault(target.id, TargetStatus("pending", now,
                                                                 "Waiting for Prometheus target discovery"))
            identifiers = {target.id for target in self.targets}
            self.statuses = {key: value for key, value in self.statuses.items() if key in identifiers}

    def status(self, target_id: str) -> TargetStatus:
        with self._lock:
            return self.statuses.get(target_id, TargetStatus())

    def dashboard_url(self, target_id: str) -> str:
        return self.dashboard_provisioner.dashboard_url(target_id)

    def healthy(self) -> bool:
        prometheus = _ready(self._prometheus_health()) if self.prometheus_attached else _alive(self.prometheus_process)
        grafana = _ready(self._grafana_health()) if self.grafana_attached else _alive(self.grafana_process)
        return prometheus and grafana

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread is not threading.current_thread():
            self._monitor_thread.join(timeout=2)
        self._stop_owned("grafana", self.grafana_process)
        self._stop_owned("prometheus", self.prometheus_process)

    def refresh_statuses(self) -> None:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.monitoring.prometheus_port}/api/v1/targets?state=active", timeout=4,
            ) as response:
                if response.status != 200:
                    raise OSError(f"Prometheus returned HTTP {response.status}")
                payload = json.load(response)
            active = {item.get("labels", {}).get("sbk_endpoint_id", ""): item
                      for item in payload.get("data", {}).get("activeTargets", [])}
            with self._lock:
                for target in self.targets:
                    observed = active.get(target.id)
                    if observed is None:
                        self.statuses[target.id] = TargetStatus("pending", _now(),
                                                                "Prometheus is discovering the endpoint")
                    else:
                        health = observed.get("health", "unknown")
                        error = observed.get("lastError", "")
                        self.statuses[target.id] = TargetStatus(
                            "up" if health == "up" else "down", observed.get("lastScrape", _now()),
                            error or f"Prometheus target {health}",
                        )
        except (OSError, ValueError, urllib.error.URLError) as error:
            print(f"WARNING: Unable to refresh Prometheus target health: {error}")

    def _monitor_statuses(self) -> None:
        while not self._stop_event.wait(5):
            self.refresh_statuses()

    def _prepare_configuration(self) -> None:
        prometheus = self.runtime_directory / "prometheus"
        grafana = self.runtime_directory / "grafana"
        for path in (prometheus / "data", grafana / "data/plugins", grafana / "logs",
                     grafana / "provisioning/datasources", grafana / "provisioning/dashboards",
                     grafana / "dashboards", self.runtime_directory / "logs"):
            path.mkdir(parents=True, exist_ok=True)
        targets = _portable(prometheus / "targets.json").replace("'", "''")
        atomic_write(prometheus / "prometheus.yml", (
            f"global:\n  scrape_interval: {self.dashboard.scrape_interval_seconds}s\n"
            f"  evaluation_interval: {self.dashboard.scrape_interval_seconds}s\n"
            "scrape_configs:\n  - job_name: sbk-dashboard\n"
            "    fallback_scrape_protocol: PrometheusText0.0.4\n    file_sd_configs:\n"
            f"      - files: ['{targets}']\n        refresh_interval: 2s\n"
            "    relabel_configs:\n      - source_labels: [sbk_metrics_path]\n"
            "        target_label: __metrics_path__\n      - regex: sbk_metrics_path\n        action: labeldrop\n"
        ).encode())
        atomic_write(grafana / "grafana.ini", (
            f"[paths]\ndata = {_portable(grafana / 'data')}\nlogs = {_portable(grafana / 'logs')}\n"
            f"plugins = {_portable(grafana / 'data/plugins')}\n"
            f"provisioning = {_portable(grafana / 'provisioning')}\n\n"
            f"[server]\nhttp_addr = 0.0.0.0\nhttp_port = {self.monitoring.grafana_port}\n\n"
            "[auth]\ndisable_login_form = true\n\n[auth.anonymous]\nenabled = true\n"
            "org_name = Main Org.\norg_role = Viewer\n\n[users]\ndefault_theme = dark\n\n"
            "[dashboards]\nmin_refresh_interval = 1s\n"
        ).encode())
        atomic_write(grafana / "provisioning/datasources/prometheus.yml", (
            "apiVersion: 1\ndatasources:\n  - name: Prometheus\n"
            f"    uid: {DATASOURCE_UID}\n    type: prometheus\n    access: proxy\n"
            f"    url: http://127.0.0.1:{self.monitoring.prometheus_port}\n"
            "    isDefault: true\n    editable: false\n"
        ).encode())
        dashboards = _portable(grafana / "dashboards").replace("'", "''")
        atomic_write(grafana / "provisioning/dashboards/sbk.yml", (
            "apiVersion: 1\nproviders:\n  - name: sbk-dashboard-managed\n    orgId: 1\n"
            "    type: file\n    disableDeletion: false\n    updateIntervalSeconds: 2\n"
            f"    allowUiUpdates: false\n    options:\n      path: '{dashboards}'\n"
        ).encode())

    def _start_prometheus(self) -> subprocess.Popen[bytes]:
        binary = resolve_on_path(self.monitoring.prometheus_binary, RuntimePlatform.current())
        if binary is None:
            raise OSError(f"Prometheus executable is not available: {self.monitoring.prometheus_binary}")
        directory = self.runtime_directory / "prometheus"
        return self._start([
            str(binary), f"--config.file={directory / 'prometheus.yml'}",
            f"--storage.tsdb.path={directory / 'data'}",
            f"--storage.tsdb.retention.time={self.dashboard.retention_days}d",
            f"--web.listen-address=0.0.0.0:{self.monitoring.prometheus_port}",
        ], self.runtime_directory / "logs/prometheus.log")

    def _start_grafana(self) -> subprocess.Popen[bytes]:
        binary = self._grafana_executable()
        command = [str(binary)]
        if _base_name(binary) == "grafana":
            command.append("server")
        command.extend([f"--homepath={self.monitoring.grafana_home.resolve()}",
                        f"--config={self.runtime_directory / 'grafana/grafana.ini'}"])
        return self._start(command, self.runtime_directory / "logs/grafana.log")

    def _grafana_executable(self) -> Path:
        current = RuntimePlatform.current()
        for base in ("grafana", "grafana-server"):
            names = (base + ".exe", base) if current.windows else (base,)
            for name in names:
                path = self.monitoring.grafana_home.resolve() / "bin" / name
                if executable(path, current):
                    return path
        raise OSError(f"Grafana executable not found under {self.monitoring.grafana_home.resolve() / 'bin'}")

    @staticmethod
    def _start(command: list[str], log_path: Path) -> subprocess.Popen[bytes]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab", buffering=0) as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        print(f"Started managed process {command[0]} (pid {process.pid})")
        return process

    @staticmethod
    def _await_ready(name: str, process: subprocess.Popen[bytes], health: str) -> None:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            code = process.poll()
            if code is not None:
                raise OSError(f"{name} exited during startup with code {code}; check whether port "
                              f"{health.split(':')[-1].split('/')[0]} is already in use")
            if _ready(health):
                time.sleep(0.5)
                if process.poll() is not None:
                    raise OSError(f"{name} exited during startup with code {process.returncode}")
                print(f"{name} ready at {health}")
                return
            time.sleep(0.25)
        raise OSError(f"{name} did not become ready within {STARTUP_TIMEOUT_SECONDS} seconds")

    def _require_available(self, name: str, port: int) -> None:
        if self.dashboard.continue_existing and not PortProcessManager.available(port):
            raise OSError(f"Port {port} is occupied but does not expose a healthy {name} service; "
                          "-continue true cannot attach to it")

    def _prometheus_health(self) -> str:
        return f"http://127.0.0.1:{self.monitoring.prometheus_port}/-/ready"

    def _grafana_health(self) -> str:
        return f"http://127.0.0.1:{self.monitoring.grafana_port}/api/health"

    def _stop_owned(self, component: str, process: subprocess.Popen[bytes] | None) -> None:
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(STOP_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    print(f"WARNING: Unable to stop managed {component} process {process.pid}")
        try:
            self.process_registry.remove(component, process.pid)
        except OSError as error:
            print(f"WARNING: Unable to update managed process ownership: {error}")


def _ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def _alive(process: subprocess.Popen[bytes] | None) -> bool:
    return process is not None and process.poll() is None


def _base_name(path: Path) -> str:
    name = path.name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _portable(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

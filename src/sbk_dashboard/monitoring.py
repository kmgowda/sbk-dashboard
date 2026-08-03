"""Prometheus/Grafana configuration, reconciliation, supervision, and target health."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sbk_dashboard.config import DashboardConfig, MonitoringConfig, RuntimePlatform, executable, resolve_on_path
from sbk_dashboard.files import atomic_write
from sbk_dashboard.models import BenchmarkTarget, TargetStatus
from sbk_dashboard.processes import (
    HttpHealthProbe,
    LifecycleController,
    LifecycleState,
    ManagedNativeService,
    ManagedProcessRegistry,
    NativeServiceSpec,
    PortProcessManager,
)
from sbk_dashboard.provisioning import (
    DATASOURCE_UID,
    GrafanaDashboardProvisioner,
    PrometheusTargetDiscovery,
    write_dashboard_mappings,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitoringStatusSummary:
    """Bounded immutable snapshot used by the periodic control-plane heartbeat."""

    stack_state: str
    prometheus_healthy: bool
    grafana_healthy: bool
    endpoints: int
    up: int
    down: int
    pending: int
    unknown: int


class ManagedMonitoringStack:
    """Facade coordinating native services and endpoint provisioning as one lifecycle."""

    def __init__(self, dashboard: DashboardConfig, monitoring: MonitoringConfig) -> None:
        self.dashboard = dashboard
        self.monitoring = monitoring
        self.runtime_directory = dashboard.data_directory / "monitoring"
        self.target_discovery = PrometheusTargetDiscovery(self.runtime_directory / "prometheus/targets.json")
        self.dashboard_provisioner = GrafanaDashboardProvisioner(
            self.runtime_directory / "grafana/dashboards", monitoring.grafana_public_url
        )
        self.process_registry = ManagedProcessRegistry(self.runtime_directory / "managed-processes.json")
        self.lifecycle = LifecycleController()
        self._operation_lock = threading.Lock()
        self._statuses: dict[str, TargetStatus] = {}
        self._targets: tuple[BenchmarkTarget, ...] = ()
        self._target_generation = 0
        self._data_lock = threading.RLock()
        self._shutdown_event = threading.Event()
        self._supervisor_thread: threading.Thread | None = None
        self._services: tuple[ManagedNativeService, ...] = ()
        self._last_status_warning: str | None = None

    def start(self, initial_targets: list[BenchmarkTarget]) -> None:
        """Start a previously new/stopped stack and assume complete ownership of created children."""
        with self._operation_lock:
            self._start(initial_targets)

    def _start(self, initial_targets: list[BenchmarkTarget]) -> None:
        if self._shutdown_event.is_set() and self.lifecycle.state == LifecycleState.NEW:
            self.lifecycle.transition(LifecycleState.STOPPED)
            raise OSError("Monitoring stack was closed before startup")
        if self.lifecycle.state == LifecycleState.STOPPED:
            self._shutdown_event.clear()
        self.lifecycle.transition(LifecycleState.STARTING)
        try:
            self._prepare_configuration()
            self.reconcile(initial_targets)
            self._validate_prometheus_configuration()
            if not self.dashboard.continue_existing:
                PortProcessManager.terminate_existing(
                    self.monitoring.prometheus_port,
                    self.monitoring.grafana_port,
                    self.process_registry,
                    self.monitoring.prometheus_bind_address,
                    self.monitoring.grafana_bind_address,
                )
            self._services = self._native_services()
            for service in self._services:
                service.start(self.dashboard.continue_existing)
            self.lifecycle.transition(LifecycleState.RUNNING)
            self.refresh_statuses()
            self._supervisor_thread = threading.Thread(
                target=self._supervise, name="sbk-native-supervisor", daemon=True
            )
            self._supervisor_thread.start()
        except BaseException:
            self.lifecycle.transition(LifecycleState.FAILED)
            self._shutdown_event.set()
            self._stop_services(strict=False)
            raise

    @property
    def state(self) -> LifecycleState:
        return self.lifecycle.state

    def reconcile(self, targets: list[BenchmarkTarget]) -> None:
        """Atomically reconcile discovery, dashboard clones, URL mappings, and bounded status state."""
        if self.lifecycle.state in {LifecycleState.STOPPING, LifecycleState.STOPPED, LifecycleState.FAILED}:
            raise RuntimeError(f"Cannot reconcile a {self.lifecycle.state.value} monitoring stack")
        snapshot = tuple(targets)
        self.target_discovery.write(list(snapshot))
        self.dashboard_provisioner.reconcile(list(snapshot))
        write_dashboard_mappings(
            self.dashboard.data_directory / "dashboard-mappings.json", list(snapshot), self.dashboard_provisioner
        )
        now = _now()
        with self._data_lock:
            previous = self._statuses
            self._targets = snapshot
            self._target_generation += 1
            self._statuses = {
                target.id: previous.get(
                    target.id, TargetStatus("pending", now, "Waiting for Prometheus target discovery")
                )
                for target in snapshot
            }

    def status(self, target_id: str) -> TargetStatus:
        with self._data_lock:
            return self._statuses.get(target_id, TargetStatus())

    def dashboard_url(self, target_id: str, browser_host: str | None = None) -> str:
        # An explicitly configured public URL is authoritative. With the default URL,
        # follow the host/IP used to reach the management UI so remote users never
        # receive a localhost-only Grafana link.
        dynamic_host = browser_host if self.monitoring.sources.get("grafana-url") == "default" else None
        return self.dashboard_provisioner.dashboard_url(target_id, dynamic_host)

    def healthy(self) -> bool:
        return self.lifecycle.state == LifecycleState.RUNNING and bool(self._services) and all(
            service.healthy() for service in self._services
        )

    def summary(self) -> MonitoringStatusSummary:
        """Return one lock-bounded status snapshot without network or process waits."""
        services = self._services
        prometheus_healthy = len(services) > 0 and services[0].healthy()
        grafana_healthy = len(services) > 1 and services[1].healthy()
        counts = {"up": 0, "down": 0, "pending": 0, "unknown": 0}
        with self._data_lock:
            endpoints = len(self._targets)
            for status in self._statuses.values():
                state = status.state if status.state in counts else "unknown"
                counts[state] += 1
        return MonitoringStatusSummary(
            self.lifecycle.state.value,
            prometheus_healthy,
            grafana_healthy,
            endpoints,
            counts["up"],
            counts["down"],
            counts["pending"],
            counts["unknown"],
        )

    def close(self) -> None:
        """Idempotently stop supervision and owned services in reverse dependency order."""
        self._shutdown_event.set()
        with self._operation_lock:
            state = self.lifecycle.state
            if state == LifecycleState.STOPPED:
                return
            if state == LifecycleState.NEW:
                self.lifecycle.transition(LifecycleState.STOPPED)
                return
            if state != LifecycleState.STOPPING:
                self.lifecycle.transition(LifecycleState.STOPPING)
            supervisor = self._supervisor_thread
            if supervisor and supervisor is not threading.current_thread():
                supervisor.join(timeout=3)
                if supervisor.is_alive():
                    LOGGER.warning("Native supervisor did not stop within 3 seconds")
            shutdown_error: OSError | None = None
            try:
                self._stop_services(strict=True)
            except OSError as error:
                shutdown_error = error
            self.lifecycle.transition(LifecycleState.STOPPED)
            if shutdown_error:
                raise shutdown_error

    def refresh_statuses(self) -> None:
        """Publish an immutable replacement status map from one bounded Prometheus response."""
        try:
            with self._data_lock:
                requested_generation = self._target_generation
            url = f"http://{self._prometheus_host()}:{self.monitoring.prometheus_port}/api/v1/targets?state=active"
            with urllib.request.urlopen(url, timeout=self.dashboard.target_health_timeout_seconds) as response:
                if response.status != 200:
                    raise OSError(f"Prometheus returned HTTP {response.status}")
                content_length = int(response.headers.get("Content-Length", "0"))
                if content_length > self.dashboard.health_response_limit_bytes:
                    raise OSError("Prometheus target health response exceeds configured limit")
                body = response.read(self.dashboard.health_response_limit_bytes + 1)
                if len(body) > self.dashboard.health_response_limit_bytes:
                    raise OSError("Prometheus target health response exceeds configured limit")
                payload = json.loads(body)
            active = {
                item.get("labels", {}).get("sbk_endpoint_id", ""): item
                for item in payload.get("data", {}).get("activeTargets", [])
            }
            with self._data_lock:
                if requested_generation != self._target_generation:
                    LOGGER.debug("Discarding Prometheus target health from an obsolete reconciliation generation")
                    return
                targets = self._targets
                next_statuses: dict[str, TargetStatus] = {}
                for target in targets:
                    observed = active.get(target.id)
                    if observed is None:
                        next_statuses[target.id] = TargetStatus(
                            "down", _now(), "Prometheus does not report the registered endpoint"
                        )
                    else:
                        health = observed.get("health", "unknown")
                        error = observed.get("lastError", "")
                        next_statuses[target.id] = TargetStatus(
                            "up" if health == "up" else "down",
                            observed.get("lastScrape", _now()),
                            error or f"Prometheus target {health}",
                        )
                self._statuses = next_statuses
            if self._last_status_warning is not None:
                LOGGER.info("Prometheus target health refresh recovered")
                self._last_status_warning = None
        except (OSError, ValueError, TypeError, urllib.error.URLError) as error:
            message = str(error)
            if message != self._last_status_warning:
                LOGGER.warning("Unable to refresh Prometheus target health: %s", message)
                self._last_status_warning = message

    def _supervise(self) -> None:
        while not self._shutdown_event.wait(self.dashboard.supervisor_interval_seconds):
            for service in self._services:
                try:
                    service.supervise()
                except Exception as error:  # keep the sole supervisor alive across platform-specific failures
                    LOGGER.warning("%s supervision failed: %s", service.spec.name, error)
            if self._services and self._services[0].healthy():
                self.refresh_statuses()

    def _native_services(self) -> tuple[ManagedNativeService, ManagedNativeService]:
        log_size = self.dashboard.process_log_size_mb * 1024 * 1024
        prometheus = ManagedNativeService(
            NativeServiceSpec(
                "Prometheus",
                "prometheus",
                self.monitoring.prometheus_port,
                self._prometheus_command,
                HttpHealthProbe(self._prometheus_health()),
                self.runtime_directory / "logs/prometheus.log",
                log_size,
                self.dashboard.process_log_backups,
                self.dashboard.prometheus_startup_timeout_seconds,
                self.monitoring.prometheus_bind_address,
            ),
            self.process_registry,
            self._shutdown_event,
        )
        grafana = ManagedNativeService(
            NativeServiceSpec(
                "Grafana",
                "grafana",
                self.monitoring.grafana_port,
                self._grafana_command,
                HttpHealthProbe(self._grafana_health()),
                self.runtime_directory / "logs/grafana-console.log",
                log_size,
                self.dashboard.process_log_backups,
                self.dashboard.grafana_startup_timeout_seconds,
                self.monitoring.grafana_bind_address,
            ),
            self.process_registry,
            self._shutdown_event,
        )
        return prometheus, grafana

    def _stop_services(self, strict: bool) -> None:
        failures: list[str] = []
        for service in reversed(self._services):
            try:
                service.stop()
            except (OSError, RuntimeError) as error:
                LOGGER.warning("Unable to stop managed %s: %s", service.spec.name, error)
                failures.append(f"{service.spec.name}: {error}")
        if strict and failures:
            raise OSError("Native service shutdown was incomplete: " + "; ".join(failures))

    def _prepare_configuration(self) -> None:
        prometheus = self.runtime_directory / "prometheus"
        grafana = self.runtime_directory / "grafana"
        for path in (
            prometheus / "data",
            grafana / "data/plugins",
            grafana / "logs",
            grafana / "provisioning/datasources",
            grafana / "provisioning/dashboards",
            grafana / "dashboards",
            self.runtime_directory / "logs",
        ):
            path.mkdir(parents=True, exist_ok=True)
        targets = _portable(prometheus / "targets.json").replace("'", "''")
        atomic_write(
            prometheus / "prometheus.yml",
            (
                f"global:\n  scrape_interval: {self.dashboard.scrape_interval_seconds}s\n"
                f"  evaluation_interval: {self.dashboard.scrape_interval_seconds}s\n"
                "scrape_configs:\n  - job_name: sbk-dashboard\n"
                "    fallback_scrape_protocol: PrometheusText0.0.4\n    file_sd_configs:\n"
                f"      - files: ['{targets}']\n        refresh_interval: 2s\n"
                "    relabel_configs:\n      - source_labels: [sbk_metrics_path]\n"
                "        target_label: __metrics_path__\n      - regex: sbk_metrics_path\n        action: labeldrop\n"
            ).encode(),
        )
        atomic_write(
            grafana / "grafana.ini",
            (
                f"[paths]\ndata = {_portable(grafana / 'data')}\nlogs = {_portable(grafana / 'logs')}\n"
                f"plugins = {_portable(grafana / 'data/plugins')}\n"
                f"provisioning = {_portable(grafana / 'provisioning')}\n\n"
                f"[server]\nhttp_addr = {self.monitoring.grafana_bind_address}\n"
                f"http_port = {self.monitoring.grafana_port}\n\n"
                "[auth]\ndisable_login_form = true\n\n[auth.anonymous]\nenabled = true\n"
                "org_name = Main Org.\norg_role = Viewer\n\n[users]\ndefault_theme = dark\n\n"
                "[dashboards]\nmin_refresh_interval = 1s\n\n[log]\nmode = console\nlevel = info\n"
            ).encode(),
        )
        atomic_write(
            grafana / "provisioning/datasources/prometheus.yml",
            (
                "apiVersion: 1\ndatasources:\n  - name: Prometheus\n"
                f"    uid: {DATASOURCE_UID}\n    type: prometheus\n    access: proxy\n"
                f"    url: http://{self._prometheus_host()}:{self.monitoring.prometheus_port}\n"
                "    isDefault: true\n    editable: false\n"
            ).encode(),
        )
        dashboards = _portable(grafana / "dashboards").replace("'", "''")
        atomic_write(
            grafana / "provisioning/dashboards/sbk.yml",
            (
                "apiVersion: 1\nproviders:\n  - name: sbk-dashboard-managed\n    orgId: 1\n"
                "    type: file\n    disableDeletion: false\n    updateIntervalSeconds: 2\n"
                f"    allowUiUpdates: false\n    options:\n      path: '{dashboards}'\n"
            ).encode(),
        )

    def _prometheus_command(self) -> list[str]:
        binary = resolve_on_path(self.monitoring.prometheus_binary, RuntimePlatform.current())
        if binary is None:
            raise OSError(f"Prometheus executable is not available: {self.monitoring.prometheus_binary}")
        directory = self.runtime_directory / "prometheus"
        return [
            str(binary),
            f"--config.file={directory / 'prometheus.yml'}",
            f"--storage.tsdb.path={directory / 'data'}",
            f"--storage.tsdb.retention.time={self.dashboard.retention_days}d",
            "--web.listen-address="
            + _listen_address(self.monitoring.prometheus_bind_address, self.monitoring.prometheus_port),
        ]

    def _validate_prometheus_configuration(self) -> None:
        current = RuntimePlatform.current()
        prometheus = resolve_on_path(self.monitoring.prometheus_binary, current)
        if prometheus is None:
            LOGGER.warning("Prometheus executable is unavailable; generated configuration was not pre-validated")
            return
        name = "promtool.exe" if current.windows else "promtool"
        adjacent = prometheus.parent / name
        promtool = adjacent if executable(adjacent, current) else resolve_on_path(Path(name), current)
        if promtool is None:
            LOGGER.warning("promtool is unavailable; generated Prometheus configuration was not pre-validated")
            return
        config = self.runtime_directory / "prometheus/prometheus.yml"
        try:
            result = subprocess.run(
                [str(promtool), "check", "config", str(config)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise OSError("promtool configuration validation timed out after 30 seconds") from error
        except (OSError, subprocess.SubprocessError) as error:
            raise OSError(f"Unable to run promtool configuration validation: {error}") from error
        if result.returncode != 0:
            raise OSError(
                f"promtool rejected generated Prometheus configuration {config}; "
                f"run '{promtool} check config {config}' for details"
            )
        LOGGER.info("Prometheus configuration validated with %s", promtool)

    def _grafana_command(self) -> list[str]:
        binary = self._grafana_executable()
        command = [str(binary)]
        if _base_name(binary) == "grafana":
            command.append("server")
        command.extend(
            [
                f"--homepath={self.monitoring.grafana_home.resolve()}",
                f"--config={self.runtime_directory / 'grafana/grafana.ini'}",
            ]
        )
        return command

    def _grafana_executable(self) -> Path:
        current = RuntimePlatform.current()
        for base in ("grafana", "grafana-server"):
            names = (base + ".exe", base) if current.windows else (base,)
            for name in names:
                path = self.monitoring.grafana_home.resolve() / "bin" / name
                if executable(path, current):
                    return path
        raise OSError(f"Grafana executable not found under {self.monitoring.grafana_home.resolve() / 'bin'}")

    def _prometheus_health(self) -> str:
        return f"http://{self._prometheus_host()}:{self.monitoring.prometheus_port}/-/ready"

    def _grafana_health(self) -> str:
        host = _consumer_host(self.monitoring.grafana_bind_address)
        return f"http://{host}:{self.monitoring.grafana_port}/api/health"

    def _prometheus_host(self) -> str:
        return _consumer_host(self.monitoring.prometheus_bind_address)


def _base_name(path: Path) -> str:
    return path.name.lower().removesuffix(".exe")


def _portable(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _consumer_host(bind_address: str) -> str:
    if bind_address == "0.0.0.0":
        return "127.0.0.1"
    if bind_address == "::":
        return "[::1]"
    return f"[{bind_address}]" if ":" in bind_address else bind_address


def _listen_address(bind_address: str, port: int) -> str:
    host = f"[{bind_address}]" if ":" in bind_address else bind_address
    return f"{host}:{port}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

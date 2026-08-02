"""SBK Dashboard command-line entry point."""

from __future__ import annotations

import ipaddress
import logging
import os
import platform
import signal
import socket
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from types import FrameType

import psutil

from sbk_dashboard.bootstrap import NativeToolBootstrap
from sbk_dashboard.config import MonitoringConfig, ParsedConfiguration, parse_configuration, parser
from sbk_dashboard.monitoring import ManagedMonitoringStack
from sbk_dashboard.registry import TargetRegistry
from sbk_dashboard.web import DashboardHttpServer

LOGGER = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def main(arguments: list[str] | None = None) -> None:
    supplied = list(sys.argv[1:] if arguments is None else arguments)
    configure_logging("INFO")
    print_runtime(supplied)
    try:
        configuration = parse_configuration(supplied)
        configure_logging(configuration.dashboard.log_level)
        LOGGER.info("Runtime platform: %s", configuration.downloads.platform.id)
        LOGGER.info("Monitoring download properties: %s", configuration.downloads.source)
        monitoring = NativeToolBootstrap().resolve(configuration.monitoring, configuration.downloads)
        run(configuration, monitoring)
    except KeyboardInterrupt:
        return
    except ValueError as error:
        LOGGER.error("%s", error)
        parser().print_help(sys.stderr)
        raise SystemExit(2) from error
    except OSError as error:
        LOGGER.error("Unable to start sbk-dashboard: %s", error)
        raise SystemExit(1) from error


def run(configuration: ParsedConfiguration, monitoring_configuration: MonitoringConfig) -> None:
    registry = TargetRegistry(configuration.dashboard.data_directory, configuration.dashboard.max_targets)
    monitoring = ManagedMonitoringStack(configuration.dashboard, monitoring_configuration)
    try:
        monitoring.start(registry.list())
        server = DashboardHttpServer(configuration.dashboard.port, registry, monitoring)
    except BaseException:
        monitoring.close()
        raise
    stopped = threading.Event()

    def stop(_signal: int, _frame: FrameType | None) -> None:
        stopped.set()

    previous_handlers: dict[
        signal.Signals, int | Callable[[int, FrameType | None], object] | None
    ] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(ValueError):
            previous_handlers[signum] = signal.signal(signum, stop)
    try:
        server.start()
        LOGGER.info(
            "SBK Dashboard listening on %s, port %s",
            configuration.dashboard.bind_address,
            configuration.dashboard.port,
        )
        LOGGER.info("Dashboard links:")
        for link in dashboard_links(configuration.dashboard.port, configuration.dashboard.bind_address):
            LOGGER.info("  %s", link)
        LOGGER.info("Authentication: disabled")
        LOGGER.info(
            "Metrics engine: managed Prometheus on %s:%s",
            monitoring_configuration.prometheus_bind_address,
            monitoring_configuration.prometheus_port,
        )
        LOGGER.info("Grafana: %s", monitoring_configuration.grafana_public_url)
        LOGGER.info("Data directory: %s", configuration.dashboard.data_directory)
        LOGGER.info(
            "Persistent history retention: %s day(s) per endpoint", configuration.dashboard.retention_days
        )
        print_effective(configuration, monitoring_configuration)
        stopped.wait()
    finally:
        shutdown_errors: list[str] = []
        try:
            server.close()
        except (OSError, RuntimeError) as error:
            shutdown_errors.append(f"HTTP server: {error}")
        try:
            monitoring.close()
        except (OSError, RuntimeError) as error:
            shutdown_errors.append(f"monitoring stack: {error}")
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        if shutdown_errors:
            raise OSError("Incomplete shutdown: " + "; ".join(shutdown_errors))


def print_runtime(arguments: list[str]) -> None:
    LOGGER.info("Python version: %s (%s)", platform.python_version(), platform.python_implementation())
    LOGGER.info("Python executable: %s", sys.executable)
    environment = "Conda" if os.environ.get("CONDA_PREFIX") else "venv" if sys.prefix != sys.base_prefix else "system"
    LOGGER.info("Python environment: %s (%s)", environment, sys.prefix)
    LOGGER.info("Supplied arguments: %s", " ".join(arguments) if arguments else "(none)")


def print_effective(configuration: ParsedConfiguration, monitoring: MonitoringConfig) -> None:
    dashboard = configuration.dashboard
    LOGGER.info("Effective configuration:")
    values = {
        "port": dashboard.port, "auth": dashboard.authentication_enabled,
        "continue": dashboard.continue_existing, "data": dashboard.data_directory,
        "retention-days": dashboard.retention_days, "scrape-seconds": dashboard.scrape_interval_seconds,
        "bind": dashboard.bind_address, "log-level": dashboard.log_level,
        "prometheus-bin": monitoring.prometheus_binary, "prometheus-port": monitoring.prometheus_port,
        "prometheus-bind": monitoring.prometheus_bind_address,
        "grafana-home": monitoring.grafana_home, "grafana-port": monitoring.grafana_port,
        "grafana-bind": monitoring.grafana_bind_address, "grafana-url": monitoring.grafana_public_url,
    }
    sources = {**dashboard.sources, **configuration.monitoring.sources}
    for name, value in values.items():
        LOGGER.info("  %s=%s [%s]", name, value, sources[name])
    properties_source = "command line" if "-monitoring-properties" in configuration.arguments else (
        "environment SBK_DASHBOARD_MONITORING_PROPERTIES"
        if os.environ.get("SBK_DASHBOARD_MONITORING_PROPERTIES") else "default"
    )
    LOGGER.info("  monitoring-properties=%s [%s]", configuration.downloads.source, properties_source)
    LOGGER.info("  monitoring-download-directory=%s [properties file]", configuration.downloads.download_directory)
    LOGGER.info("  monitoring-install-directory=%s [properties file]", configuration.downloads.install_directory)
    operational = {
        "http-workers": (dashboard.http_workers, "SBK_DASHBOARD_HTTP_WORKERS"),
        "http-queue-capacity": (dashboard.http_queue_capacity, "SBK_DASHBOARD_HTTP_QUEUE"),
        "request-timeout-seconds": (dashboard.request_timeout_seconds, "SBK_DASHBOARD_REQUEST_TIMEOUT_SECONDS"),
        "health-response-limit-bytes": (dashboard.health_response_limit_bytes, "SBK_DASHBOARD_HEALTH_RESPONSE_MB"),
        "supervisor-seconds": (dashboard.supervisor_interval_seconds, "SBK_DASHBOARD_SUPERVISOR_SECONDS"),
        "process-log-size-mb": (dashboard.process_log_size_mb, "SBK_DASHBOARD_PROCESS_LOG_MB"),
        "process-log-backups": (dashboard.process_log_backups, "SBK_DASHBOARD_PROCESS_LOG_BACKUPS"),
        "max-targets": (dashboard.max_targets, "SBK_DASHBOARD_MAX_TARGETS"),
        "target-health-timeout-seconds": (
            dashboard.target_health_timeout_seconds,
            "SBK_DASHBOARD_TARGET_HEALTH_TIMEOUT_SECONDS",
        ),
        "prometheus-startup-timeout-seconds": (
            dashboard.prometheus_startup_timeout_seconds,
            "SBK_DASHBOARD_PROMETHEUS_STARTUP_TIMEOUT_SECONDS",
        ),
        "grafana-startup-timeout-seconds": (
            dashboard.grafana_startup_timeout_seconds,
            "SBK_DASHBOARD_GRAFANA_STARTUP_TIMEOUT_SECONDS",
        ),
    }
    for name, (value, environment) in operational.items():
        source = f"environment {environment}" if os.environ.get(environment) else "default"
        LOGGER.info("  %s=%s [%s]", name, value, source)


def dashboard_links(port: int, bind_address: str = "0.0.0.0") -> list[str]:
    if bind_address not in {"0.0.0.0", "::"}:
        if bind_address == "127.0.0.1":
            return [f"http://localhost:{port}/", f"http://127.0.0.1:{port}/"]
        host = f"[{bind_address}]" if ":" in bind_address else bind_address
        return [f"http://{host}:{port}/"]
    links = [f"http://localhost:{port}/", f"http://127.0.0.1:{port}/"]
    if bind_address == "::":
        links.append(f"http://[::1]:{port}/")
    addresses: set[str] = set()
    try:
        for values in psutil.net_if_addrs().values():
            for value in values:
                if value.family not in {socket.AF_INET, socket.AF_INET6}:
                    continue
                raw = value.address.split("%", 1)[0]
                address = ipaddress.ip_address(raw)
                if address.is_unspecified or address.is_loopback or address.is_link_local or address.is_multicast:
                    continue
                host = value.address.replace("%", "%25")
                addresses.add(f"[{host}]" if address.version == 6 else host)
    except (OSError, ValueError) as error:
        LOGGER.warning("Unable to discover network dashboard addresses: %s. Loopback links remain available.", error)
    links.extend(f"http://{address}:{port}/" for address in sorted(addresses))
    return links


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        stream=sys.stderr,
        force=True,
    )

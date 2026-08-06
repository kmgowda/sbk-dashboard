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
import webbrowser
from collections.abc import Callable, Mapping
from contextlib import suppress
from importlib.resources import files
from types import FrameType

import psutil

from sbk_dashboard.bootstrap import NativeToolBootstrap
from sbk_dashboard.config import MonitoringConfig, ParsedConfiguration, parse_configuration, parser
from sbk_dashboard.monitoring import ManagedMonitoringStack
from sbk_dashboard.registry import TargetRegistry
from sbk_dashboard.version import VERSION
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
        links = dashboard_links(configuration.dashboard.port, configuration.dashboard.bind_address)
        for link in links:
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
        open_landing_page(links[0])
        while not stopped.wait(configuration.dashboard.status_interval_seconds):
            log_status(server, monitoring)
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
    try:
        banner = files("sbk_dashboard").joinpath("resources/banner.txt").read_text(encoding="utf-8").rstrip()
        LOGGER.info("\n%s", banner)
    except OSError as error:
        LOGGER.warning("Unable to load startup banner: %s", error)
    LOGGER.info("SBK Dashboard version: %s", VERSION)
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
        "status-seconds": dashboard.status_interval_seconds,
        "default-target-host": dashboard.default_target_host,
        "prometheus-bin": monitoring.prometheus_binary, "prometheus-port": monitoring.prometheus_port,
        "prometheus-bind": monitoring.prometheus_bind_address,
        "grafana-home": monitoring.grafana_home, "grafana-port": monitoring.grafana_port,
        "grafana-bind": monitoring.grafana_bind_address, "grafana-url": monitoring.grafana_public_url,
    }
    sources = {**dashboard.sources, **configuration.monitoring.sources}
    for name, value in values.items():
        LOGGER.info("  %s=%s [%s]", name, value, sources[name])
    LOGGER.info(
        "  monitoring-properties=%s [%s]",
        configuration.downloads.source,
        configuration.downloads.selection_source,
    )
    LOGGER.info("  monitoring-download-directory=%s [properties file]", configuration.downloads.download_directory)
    LOGGER.info("  monitoring-install-directory=%s [properties file]", configuration.downloads.install_directory)
    LOGGER.info("  monitoring-max-download-bytes=%s [properties file]", configuration.downloads.max_download_bytes)
    operational = {
        "http-workers": dashboard.http_workers,
        "http-queue-capacity": dashboard.http_queue_capacity,
        "request-timeout-seconds": dashboard.request_timeout_seconds,
        "health-response-limit-bytes": dashboard.health_response_limit_bytes,
        "supervisor-seconds": dashboard.supervisor_interval_seconds,
        "process-log-size-mb": dashboard.process_log_size_mb,
        "process-log-backups": dashboard.process_log_backups,
        "max-targets": dashboard.max_targets,
        "target-health-timeout-seconds": dashboard.target_health_timeout_seconds,
        "prometheus-startup-timeout-seconds": dashboard.prometheus_startup_timeout_seconds,
        "grafana-startup-timeout-seconds": dashboard.grafana_startup_timeout_seconds,
    }
    for name, value in operational.items():
        LOGGER.info("  %s=%s [%s]", name, value, dashboard.sources[name])


def log_status(server: DashboardHttpServer, monitoring: ManagedMonitoringStack) -> None:
    """Log one concise, non-blocking status snapshot."""
    try:
        summary = monitoring.summary()
        clients = server.client_activity()
        LOGGER.info(
            "Status: server=%s stack=%s prometheus=%s grafana=%s endpoints=%s up=%s down=%s pending=%s "
            "unknown=%s clients_recent=%s landing_clients_2m=%s grafana_opens_5m=%s",
            server.lifecycle.state.value,
            summary.stack_state,
            "up" if summary.prometheus_healthy else "down",
            "up" if summary.grafana_healthy else "down",
            summary.endpoints,
            summary.up,
            summary.down,
            summary.pending,
            summary.unknown,
            clients.total,
            clients.landing,
            clients.grafana_opens,
        )
    except Exception as error:  # a diagnostic heartbeat must never terminate the production server
        LOGGER.warning("Unable to produce periodic status: %s", error)


def open_landing_page(
    url: str,
    environment: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    os_name: str | None = None,
    opener: Callable[..., bool] | None = None,
) -> bool:
    """Open *url* in a desktop browser, without disturbing SSH or headless sessions."""
    selected_environment = os.environ if environment is None else environment
    if any(selected_environment.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        LOGGER.info("Automatic browser launch skipped: SSH session detected")
        return False
    if selected_environment.get("CI"):
        LOGGER.info("Automatic browser launch skipped: CI environment detected")
        return False
    selected_platform = sys.platform if platform_name is None else platform_name
    selected_os = os.name if os_name is None else os_name
    if selected_os == "nt" and selected_environment.get("SESSIONNAME", "").casefold() == "services":
        LOGGER.info("Automatic browser launch skipped: non-interactive Windows service session detected")
        return False
    graphical_session = selected_os == "nt" or selected_platform == "darwin" or any(
        selected_environment.get(name) for name in ("DISPLAY", "WAYLAND_DISPLAY")
    )
    if not graphical_session:
        LOGGER.info("Automatic browser launch skipped: no graphical desktop session detected")
        return False
    selected_opener = webbrowser.open if opener is None else opener
    try:
        opened = selected_opener(url, new=2, autoraise=True)
    except Exception as error:  # browser backends are external and must never stop the server
        LOGGER.warning("Unable to open landing page automatically: %s", error)
        return False
    if opened:
        LOGGER.info("Opened landing page in the default web browser: %s", url)
    else:
        LOGGER.warning("No graphical web browser accepted the landing page: %s", url)
    return opened


def dashboard_links(port: int, bind_address: str = "0.0.0.0") -> list[str]:
    if bind_address not in {"0.0.0.0", "::"}:
        if bind_address == "127.0.0.1":
            return [f"http://localhost:{port}/", f"http://127.0.0.1:{port}/"]
        host = f"[{bind_address}]" if ":" in bind_address else bind_address
        return [f"http://{host}:{port}/"]
    family = socket.AF_INET6 if bind_address == "::" else socket.AF_INET
    links = (
        [f"http://[::1]:{port}/"]
        if family == socket.AF_INET6
        else [f"http://localhost:{port}/", f"http://127.0.0.1:{port}/"]
    )
    addresses: set[str] = set()
    try:
        for values in psutil.net_if_addrs().values():
            for value in values:
                if value.family != family:
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

# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

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
from sbk_dashboard.contracts import (
    BOOTSTRAP_DIAGNOSTICS_REPORTED_ENVIRONMENT,
    BOOTSTRAP_RUNTIME_KIND_ENVIRONMENT,
    BOOTSTRAP_RUNTIME_PATH_ENVIRONMENT,
    BOOTSTRAP_RUNTIME_STATE_ENVIRONMENT,
)
from sbk_dashboard.monitoring import ManagedMonitoringStack
from sbk_dashboard.processes import PortProcessManager
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
        monitoring = select_native_ports(
            configuration.monitoring,
            configuration.dashboard.continue_existing,
        )
        monitoring = NativeToolBootstrap().resolve(monitoring, configuration.downloads)
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
    shutdown_signals = [signal.SIGINT, signal.SIGTERM]
    break_signal = vars(signal).get("SIGBREAK")
    if isinstance(break_signal, signal.Signals):
        shutdown_signals.append(break_signal)
    for signum in shutdown_signals:
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


def select_native_ports(
    monitoring: MonitoringConfig, continue_existing: bool = False
) -> MonitoringConfig:
    """Select non-conflicting native ports only when their built-in defaults were used."""
    if continue_existing:
        _log_native_port_selection("Prometheus", monitoring, "prometheus", continue_existing=True)
        _log_native_port_selection("Grafana", monitoring, "grafana", continue_existing=True)
        return monitoring
    selected_prometheus = monitoring.prometheus_port
    if monitoring.sources["prometheus-port"] == "default":
        selected_prometheus = PortProcessManager.find_available(
            monitoring.prometheus_port,
            monitoring.prometheus_bind_address,
        )
        if selected_prometheus != monitoring.prometheus_port:
            LOGGER.warning(
                "Default Prometheus port %s is unavailable; selected port %s",
                monitoring.prometheus_port,
                selected_prometheus,
            )
    elif monitoring.port_was_supplied("prometheus"):
        PortProcessManager.require_available(
            "Prometheus",
            monitoring.prometheus_port,
            monitoring.prometheus_bind_address,
            monitoring.sources["prometheus-port"],
        )

    selected_grafana = monitoring.grafana_port
    if monitoring.sources["grafana-port"] == "default":
        selected_grafana = PortProcessManager.find_available(
            monitoring.grafana_port,
            monitoring.grafana_bind_address,
            {selected_prometheus},
        )
        if selected_grafana != monitoring.grafana_port:
            LOGGER.warning(
                "Default Grafana port %s is unavailable; selected port %s",
                monitoring.grafana_port,
                selected_grafana,
            )
    elif monitoring.port_was_supplied("grafana"):
        PortProcessManager.require_available(
            "Grafana",
            monitoring.grafana_port,
            monitoring.grafana_bind_address,
            monitoring.sources["grafana-port"],
        )
    if selected_prometheus == selected_grafana:
        raise OSError(
            f"Prometheus and Grafana cannot both use port {selected_prometheus}; "
            "supply distinct available native ports"
        )
    selected = monitoring.with_runtime_ports(selected_prometheus, selected_grafana)
    _log_native_port_selection("Prometheus", selected, "prometheus")
    _log_native_port_selection("Grafana", selected, "grafana")
    return selected


def _log_native_port_selection(
    name: str,
    monitoring: MonitoringConfig,
    component: str,
    continue_existing: bool = False,
) -> None:
    port = getattr(monitoring, f"{component}_port")
    bind_address = getattr(monitoring, f"{component}_bind_address")
    source = monitoring.sources[f"{component}-port"]
    if source == "default":
        reason = "built-in default"
    elif source.startswith("automatic fallback from "):
        original = source.removeprefix("automatic fallback from ")
        reason = f"automatically selected because default port {original} was already in use"
    else:
        reason = f"user supplied via {source}"
    if continue_existing:
        reason += "; reserved for -continue true compatibility/health attachment"
    LOGGER.info("%s port: %s on %s (%s)", name, port, bind_address, reason)


def print_runtime(arguments: list[str]) -> None:
    try:
        banner = files("sbk_dashboard").joinpath("resources/banner.txt").read_text(encoding="utf-8").rstrip()
        LOGGER.info("\n%s", banner)
    except OSError as error:
        LOGGER.warning("Unable to load startup banner: %s", error)
    LOGGER.info("SBK Dashboard version: %s", VERSION)
    if not os.environ.get(BOOTSTRAP_DIAGNOSTICS_REPORTED_ENVIRONMENT):
        LOGGER.info(
            "Operating system: %s %s (%s; %s)",
            platform.system(),
            platform.release(),
            platform.machine(),
            platform.platform(),
        )
        LOGGER.info("Python version: %s (%s)", platform.python_version(), platform.python_implementation())
        LOGGER.info("Python executable: %s", sys.executable)
        environment = (
            "Conda"
            if os.environ.get("CONDA_PREFIX")
            else "venv"
            if sys.prefix != sys.base_prefix
            else "system"
        )
        LOGGER.info("Python environment: %s (%s)", environment, sys.prefix)
        runtime_kind = os.environ.get(BOOTSTRAP_RUNTIME_KIND_ENVIRONMENT)
        runtime_state = os.environ.get(BOOTSTRAP_RUNTIME_STATE_ENVIRONMENT)
        runtime_path = os.environ.get(BOOTSTRAP_RUNTIME_PATH_ENVIRONMENT)
        if runtime_kind:
            LOGGER.info("Bootstrap runtime: %s", runtime_kind)
        if runtime_state:
            LOGGER.info("Runtime preparation: %s", runtime_state)
        if runtime_path:
            LOGGER.info("Runtime location: %s", runtime_path)
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
        "max-comparison-targets": dashboard.max_comparison_targets,
        "default-target-host": dashboard.default_target_host,
        "prometheus-bin": monitoring.prometheus_binary, "prometheus-port": monitoring.prometheus_port,
        "prometheus-bind": monitoring.prometheus_bind_address,
        "grafana-home": monitoring.grafana_home, "grafana-port": monitoring.grafana_port,
        "grafana-bind": monitoring.grafana_bind_address, "grafana-url": monitoring.grafana_public_url,
    }
    sources = {**dashboard.sources, **monitoring.sources}
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
                addresses.add(f"[{host}]" if isinstance(address, ipaddress.IPv6Address) else host)
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

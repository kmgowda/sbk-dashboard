"""SBK Dashboard command-line entry point."""

from __future__ import annotations

import ipaddress
import os
import platform
import signal
import socket
import sys
import threading
from contextlib import suppress

import psutil

from sbk_dashboard.bootstrap import NativeToolBootstrap
from sbk_dashboard.config import MonitoringConfig, ParsedConfiguration, parse_configuration, parser
from sbk_dashboard.monitoring import ManagedMonitoringStack
from sbk_dashboard.registry import TargetRegistry
from sbk_dashboard.web import DashboardHttpServer


def main(arguments: list[str] | None = None) -> None:
    supplied = list(sys.argv[1:] if arguments is None else arguments)
    print_runtime(supplied)
    try:
        configuration = parse_configuration(supplied)
        print(f"Runtime platform: {configuration.downloads.platform.id}")
        print(f"Monitoring download properties: {configuration.downloads.source}")
        monitoring = NativeToolBootstrap().resolve(configuration.monitoring, configuration.downloads)
        run(configuration, monitoring)
    except KeyboardInterrupt:
        return
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        parser().print_help(sys.stderr)
        raise SystemExit(2) from error
    except OSError as error:
        print(f"Unable to start sbk-dashboard: {error}", file=sys.stderr)
        raise SystemExit(1) from error


def run(configuration: ParsedConfiguration, monitoring_configuration: MonitoringConfig) -> None:
    registry = TargetRegistry(configuration.dashboard.data_directory)
    monitoring = ManagedMonitoringStack(configuration.dashboard, monitoring_configuration, registry.list())
    try:
        server = DashboardHttpServer(configuration.dashboard.port, registry, monitoring)
    except BaseException:
        monitoring.close()
        raise
    stopped = threading.Event()

    def stop(_signal: int | None = None, _frame: object | None = None) -> None:
        stopped.set()

    previous_handlers: dict[int, object] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(ValueError):
            previous_handlers[signum] = signal.signal(signum, stop)
    try:
        server.start()
        print(f"SBK Dashboard listening on all interfaces, port {configuration.dashboard.port}")
        print("Dashboard links:")
        for link in dashboard_links(configuration.dashboard.port):
            print(f"  {link}")
        print("Authentication: disabled")
        print(f"Metrics engine: managed Prometheus at http://127.0.0.1:{monitoring_configuration.prometheus_port}")
        print(f"Grafana: {monitoring_configuration.grafana_public_url}")
        print(f"Data directory: {configuration.dashboard.data_directory}")
        print(f"Persistent history retention: {configuration.dashboard.retention_days} day(s) per endpoint")
        print_effective(configuration, monitoring_configuration)
        stopped.wait()
    finally:
        server.close()
        monitoring.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def print_runtime(arguments: list[str]) -> None:
    print(f"Python version: {platform.python_version()} ({platform.python_implementation()})")
    print(f"Python executable: {sys.executable}")
    environment = "Conda" if os.environ.get("CONDA_PREFIX") else "venv" if sys.prefix != sys.base_prefix else "system"
    print(f"Python environment: {environment} ({sys.prefix})")
    print(f"Supplied arguments: {' '.join(arguments) if arguments else '(none)'}")


def print_effective(configuration: ParsedConfiguration, monitoring: MonitoringConfig) -> None:
    dashboard = configuration.dashboard
    print("Effective configuration:")
    values = {
        "port": dashboard.port, "auth": dashboard.authentication_enabled,
        "continue": dashboard.continue_existing, "data": dashboard.data_directory,
        "retention-days": dashboard.retention_days, "scrape-seconds": dashboard.scrape_interval_seconds,
        "prometheus-bin": monitoring.prometheus_binary, "prometheus-port": monitoring.prometheus_port,
        "grafana-home": monitoring.grafana_home, "grafana-port": monitoring.grafana_port,
        "grafana-url": monitoring.grafana_public_url,
    }
    sources = {**dashboard.sources, **configuration.monitoring.sources}
    for name, value in values.items():
        print(f"  {name}={value} [{sources[name]}]")
    properties_source = "command line" if "-monitoring-properties" in configuration.arguments else (
        "environment SBK_DASHBOARD_MONITORING_PROPERTIES"
        if os.environ.get("SBK_DASHBOARD_MONITORING_PROPERTIES") else "default"
    )
    print(f"  monitoring-properties={configuration.downloads.source} [{properties_source}]")
    print(f"  monitoring-download-directory={configuration.downloads.download_directory} [properties file]")
    print(f"  monitoring-install-directory={configuration.downloads.install_directory} [properties file]")


def dashboard_links(port: int) -> list[str]:
    links = [f"http://localhost:{port}/", f"http://127.0.0.1:{port}/"]
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
        print(f"WARNING: Unable to discover network dashboard addresses: {error}. Loopback links remain available.")
    links.extend(f"http://{address}:{port}/" for address in sorted(addresses))
    return links

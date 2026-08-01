"""Command-line, environment, platform, and native-download configuration."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_PORT = 9721
DEFAULT_PROMETHEUS_PORT = 9090
DEFAULT_GRAFANA_PORT = 3000
DEFAULT_RETENTION_DAYS = 7


def _select(option: str | None, environment: dict[str, str], variable: str, default: str) -> tuple[str, str]:
    if option is not None:
        value = option.strip()
        if not value:
            raise ValueError("Configuration option must not be blank")
        return value, "command line"
    value = environment.get(variable, "").strip()
    return (value, f"environment {variable}") if value else (default, "default")


def _positive(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if parsed < 1:
        raise ValueError(f"{name} must be positive")
    return parsed


def _port(value: str, name: str) -> int:
    parsed = _positive(value, name)
    if parsed > 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return parsed


def _boolean(value: str, name: str) -> bool:
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(f"-{name} must be true or false")


@dataclass(frozen=True)
class RuntimePlatform:
    operating_system: str
    architecture: str

    @property
    def id(self) -> str:
        return f"{self.operating_system}-{self.architecture}"

    @property
    def windows(self) -> bool:
        return self.operating_system == "windows"

    @classmethod
    def current(cls) -> RuntimePlatform:
        return cls.from_names(platform.system(), platform.machine())

    @classmethod
    def from_names(cls, os_name: str, architecture: str) -> RuntimePlatform:
        system = os_name.lower()
        if "darwin" in system or "mac" in system:
            normalized_os = "macos"
        elif "windows" in system or system.startswith("win"):
            normalized_os = "windows"
        elif "linux" in system:
            normalized_os = "linux"
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")
        normalized_arch = architecture.lower().replace("-", "_")
        if normalized_arch in {"amd64", "x86_64", "x64"}:
            normalized_arch = "x86_64"
        elif normalized_arch in {"aarch64", "arm64"}:
            normalized_arch = "arm64"
        else:
            raise ValueError(f"Unsupported architecture: {architecture}")
        return cls(normalized_os, normalized_arch)


@dataclass(frozen=True)
class DashboardConfig:
    port: int
    authentication_enabled: bool
    continue_existing: bool
    data_directory: Path
    scrape_interval_seconds: int
    retention_days: int
    sources: dict[str, str]
    http_workers: int = 8
    http_queue_capacity: int = 64
    request_timeout_seconds: int = 15
    health_response_limit_bytes: int = 4 * 1024 * 1024
    supervisor_interval_seconds: int = 5
    process_log_size_mb: int = 10
    process_log_backups: int = 3
    max_targets: int = 10_000


@dataclass(frozen=True)
class MonitoringConfig:
    prometheus_binary: Path
    grafana_home: Path
    prometheus_port: int
    grafana_port: int
    grafana_public_url: str
    sources: dict[str, str]

    def with_tools(self, prometheus_binary: Path, grafana_home: Path) -> MonitoringConfig:
        return replace(self, prometheus_binary=prometheus_binary, grafana_home=grafana_home)


@dataclass(frozen=True)
class ToolArchive:
    url: str
    file_name: str
    sha256: str
    archive_directory: Path
    executable: Path
    archive_format: str


@dataclass(frozen=True)
class DownloadConfig:
    download_directory: Path
    install_directory: Path
    prometheus: ToolArchive
    grafana: ToolArchive
    platform: RuntimePlatform
    source: str


@dataclass(frozen=True)
class ParsedConfiguration:
    dashboard: DashboardConfig
    monitoring: MonitoringConfig
    downloads: DownloadConfig
    arguments: list[str]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="sbk-dashboard",
        description="SBK/SBM dashboard control server with managed Prometheus and Grafana",
        epilog="Prometheus and Grafana run as child processes; Docker is not required.",
    )
    result.add_argument("-port", default=str(DEFAULT_PORT), metavar="port", help="dashboard HTTP port (default: 9721)")
    result.add_argument("-auth", default="false", metavar="true|false", help="false only; reserved for future use")
    result.add_argument("-continue", dest="continue_existing", default="false", metavar="true|false",
                        help="reuse healthy existing Prometheus/Grafana processes (default: false)")
    result.add_argument("-data", "--data-dir", dest="data", metavar="directory", help="persistent data directory")
    result.add_argument("-retention", "--retention-days", dest="retention", metavar="days",
                        help="Prometheus retention days (default: 7)")
    result.add_argument("-prometheus-bin", metavar="path", help="Prometheus executable")
    result.add_argument("-prometheus-port", metavar="port", help="managed Prometheus port (default: 9090)")
    result.add_argument("-grafana-home", metavar="directory", help="Grafana installation home")
    result.add_argument("-grafana-port", metavar="port", help="managed Grafana port (default: 3000)")
    result.add_argument("-grafana-url", metavar="url", help="browser-accessible Grafana base URL")
    result.add_argument("-monitoring-properties", metavar="file", help="native download properties file")
    return result


def parse_configuration(arguments: list[str], environment: dict[str, str] | None = None,
                        runtime_platform: RuntimePlatform | None = None) -> ParsedConfiguration:
    environment = dict(os.environ if environment is None else environment)
    namespace = parser().parse_args(arguments)
    port = _port(namespace.port, "port")
    authentication = _boolean(namespace.auth, "auth")
    if authentication:
        raise ValueError("Authentication is reserved for a future release; use -auth false")
    continue_existing = _boolean(namespace.continue_existing, "continue")
    data, data_source = _select(namespace.data, environment, "SBK_DASHBOARD_DATA_DIR",
                                str(Path.home() / ".sbk-dashboard"))
    retention, retention_source = _select(namespace.retention, environment,
                                           "SBK_DASHBOARD_DISK_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    scrape, scrape_source = _select(None, environment, "SBK_DASHBOARD_SCRAPE_SECONDS", "5")
    http_workers = _bounded_environment(environment, "SBK_DASHBOARD_HTTP_WORKERS", 8, 1, 128)
    http_queue = _bounded_environment(environment, "SBK_DASHBOARD_HTTP_QUEUE", 64, 0, 10_000)
    request_timeout = _bounded_environment(environment, "SBK_DASHBOARD_REQUEST_TIMEOUT_SECONDS", 15, 1, 300)
    health_limit_mb = _bounded_environment(environment, "SBK_DASHBOARD_HEALTH_RESPONSE_MB", 4, 1, 64)
    supervisor_interval = _bounded_environment(environment, "SBK_DASHBOARD_SUPERVISOR_SECONDS", 5, 1, 60)
    process_log_size = _bounded_environment(environment, "SBK_DASHBOARD_PROCESS_LOG_MB", 10, 1, 1024)
    process_log_backups = _bounded_environment(environment, "SBK_DASHBOARD_PROCESS_LOG_BACKUPS", 3, 0, 100)
    max_targets = _bounded_environment(environment, "SBK_DASHBOARD_MAX_TARGETS", 10_000, 1, 1_000_000)
    dashboard = DashboardConfig(
        port, False, continue_existing, Path(data).expanduser().resolve(), _positive(scrape, "scrape interval"),
        _positive(retention, "retention"),
        {"port": "command line" if "-port" in arguments else "default",
         "auth": "command line" if "-auth" in arguments else "default",
         "continue": "command line" if "-continue" in arguments else "default", "data": data_source,
         "retention-days": retention_source, "scrape-seconds": scrape_source},
        http_workers,
        http_queue,
        request_timeout,
        health_limit_mb * 1024 * 1024,
        supervisor_interval,
        process_log_size,
        process_log_backups,
        max_targets,
    )
    prometheus, prometheus_source = _select(namespace.prometheus_bin, environment,
                                             "SBK_DASHBOARD_PROMETHEUS_BIN", "prometheus")
    grafana, grafana_source = _select(namespace.grafana_home, environment,
                                      "SBK_DASHBOARD_GRAFANA_HOME", default_grafana_home())
    prometheus_port, prometheus_port_source = _select(namespace.prometheus_port, environment,
                                                       "SBK_DASHBOARD_PROMETHEUS_PORT", "9090")
    grafana_port, grafana_port_source = _select(namespace.grafana_port, environment,
                                                "SBK_DASHBOARD_GRAFANA_PORT", "3000")
    selected_grafana_port = _port(grafana_port, "grafana port")
    public_url, url_source = _select(namespace.grafana_url, environment, "SBK_DASHBOARD_GRAFANA_URL",
                                     f"http://localhost:{selected_grafana_port}")
    parsed_url = urlparse(public_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("Grafana URL must be an absolute HTTP or HTTPS URL")
    monitoring = MonitoringConfig(
        Path(prometheus).expanduser(), Path(grafana).expanduser(), _port(prometheus_port, "prometheus port"),
        selected_grafana_port, public_url.rstrip("/"),
        {"prometheus-bin": prometheus_source, "grafana-home": grafana_source,
         "prometheus-port": prometheus_port_source, "grafana-port": grafana_port_source,
         "grafana-url": url_source},
    )
    downloads = load_download_config(namespace.monitoring_properties, dashboard.data_directory, environment,
                                     runtime_platform)
    return ParsedConfiguration(dashboard, monitoring, downloads, list(arguments))


def default_grafana_home() -> str:
    if os.name == "nt":
        return str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "GrafanaLabs" / "grafana")
    return "/usr/share/grafana"


def _bounded_environment(
    environment: dict[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    value = environment.get(name, "").strip()
    try:
        selected = default if not value else int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not minimum <= selected <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return selected


def _read_properties(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    continuation = ""
    for raw_line in text.splitlines():
        line = continuation + raw_line.strip()
        if line.endswith("\\"):
            continuation = line[:-1]
            continue
        continuation = ""
        if not line or line.startswith(("#", "!")):
            continue
        match = re.match(r"([^:=\s]+)\s*[:=]\s*(.*)", line)
        if match:
            result[match.group(1)] = match.group(2).strip()
    return result


def load_download_config(option: str | None, data_directory: Path, environment: dict[str, str],
                         runtime_platform: RuntimePlatform | None = None) -> DownloadConfig:
    selected_platform = runtime_platform or RuntimePlatform.current()
    resource = files("sbk_dashboard").joinpath("resources/monitoring-download.properties")
    properties = _read_properties(resource.read_text(encoding="utf-8"))
    candidates: list[Path] = []
    explicit = option or environment.get("SBK_DASHBOARD_MONITORING_PROPERTIES")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        candidates.extend([Path("conf/monitoring-download.properties"), Path("config/monitoring-download.properties")])
    external = next((item.resolve() for item in candidates if item.is_file()), None)
    if explicit and external is None:
        raise ValueError(f"Monitoring properties file does not exist: {Path(explicit).expanduser().resolve()}")
    source = "packaged monitoring-download.properties"
    if external:
        overrides = _read_properties(external.read_text(encoding="utf-8"))
        properties.update(overrides)
        for tool in ("prometheus", "grafana"):
            for suffix in ("download.url", "download.file", "download.sha256", "archive.directory", "executable",
                           "archive.format"):
                legacy = f"{tool}.{suffix}"
                selected = f"{tool}.{selected_platform.id}.{suffix}"
                if legacy in overrides and selected not in overrides:
                    properties[selected] = overrides[legacy]
        source = str(external)
    variables = {
        "data.directory": str(data_directory.resolve()), "user.home": str(Path.home()),
        "os.arch": selected_platform.architecture, "os.name": selected_platform.operating_system,
    }

    def expand(value: str) -> str:
        for name, replacement in variables.items():
            value = value.replace("${" + name + "}", replacement)
        if "${" in value:
            raise ValueError(f"Unknown placeholder in monitoring property: {value}")
        return value

    def required(name: str) -> str:
        value = properties.get(name, "").strip()
        if not value:
            raise ValueError(f"Missing monitoring property: {name}")
        return value

    def archive(tool: str) -> ToolArchive:
        prefix = f"{tool}.{selected_platform.id}"
        legacy = tool

        def selected(suffix: str) -> str:
            return properties.get(f"{prefix}.{suffix}", "").strip() or required(f"{legacy}.{suffix}")

        url = expand(selected("download.url"))
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"{tool} download URL must be an absolute HTTPS URL")
        file_name = expand(selected("download.file"))
        if Path(file_name).name != file_name:
            raise ValueError(f"{tool} download file must be a file name")
        checksum = selected("download.sha256").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError(f"{tool} SHA-256 must contain 64 hexadecimal characters")
        archive_directory = _safe_relative(selected("archive.directory"), tool)
        executable = _safe_relative(selected("executable"), tool)
        archive_format = selected("archive.format").lower()
        if archive_format not in {"tar.gz", "zip"}:
            raise ValueError(f"Unsupported archive format: {archive_format}")
        return ToolArchive(url, file_name, checksum, archive_directory, executable, archive_format)

    downloads = Path(expand(required("download.directory"))).expanduser().resolve()
    installs = Path(expand(required("install.directory"))).expanduser().resolve()
    return DownloadConfig(downloads, installs, archive("prometheus"), archive("grafana"), selected_platform, source)


def _safe_relative(value: str, tool: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value.strip():
        raise ValueError(f"{tool} archive paths must be safe relative paths")
    return path


def resolve_on_path(path: Path, runtime_platform: RuntimePlatform) -> Path | None:
    names = [path.name]
    if runtime_platform.windows and path.suffix.lower() != ".exe":
        names.insert(0, path.name + ".exe")
    if path.is_absolute() or len(path.parts) > 1:
        candidate = path.expanduser().resolve()
        return candidate if executable(candidate, runtime_platform) else None
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


def executable(path: Path, runtime_platform: RuntimePlatform) -> bool:
    return path.is_file() and (runtime_platform.windows or os.access(path, os.X_OK))

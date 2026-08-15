"""Pure path construction for portable and per-instance runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sbk_dashboard.contracts import DEFAULT_DASHBOARD_PORT, DEFAULT_HOME_DIRECTORY_NAME


def validate_dedicated_home(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise ValueError("SBK_DASHBOARD_HOME must be a dedicated subdirectory, not a filesystem or home root")
    return resolved


@dataclass(frozen=True)
class PortableHomeLayout:
    root: Path

    @classmethod
    def from_value(cls, value: str | None) -> PortableHomeLayout:
        selected = Path(value) if value and value.strip() else Path.home() / DEFAULT_HOME_DIRECTORY_NAME
        return cls(validate_dedicated_home(selected))

    @property
    def launcher(self) -> Path:
        return self.root / "launcher"

    @property
    def pip_cache(self) -> Path:
        return self.root / "cache" / "pip"

    @property
    def application(self) -> Path:
        return self.root / "app"

    def data_directory(self, management_port: int) -> Path:
        if management_port == DEFAULT_DASHBOARD_PORT:
            return self.root
        return self.root / "instances" / str(management_port)


@dataclass(frozen=True)
class MonitoringRuntimeLayout:
    root: Path

    @property
    def process_registry(self) -> Path:
        return self.root / "managed-processes.json"

    @property
    def prometheus(self) -> Path:
        return self.root / "prometheus"

    @property
    def prometheus_targets(self) -> Path:
        return self.prometheus / "targets.json"

    @property
    def grafana(self) -> Path:
        return self.root / "grafana"

    @property
    def grafana_dashboards(self) -> Path:
        return self.grafana / "dashboards"

    @property
    def logs(self) -> Path:
        return self.root / "logs"


@dataclass(frozen=True)
class DashboardDataLayout:
    root: Path

    @property
    def targets(self) -> Path:
        return self.root / "targets.json"

    @property
    def dashboard_mappings(self) -> Path:
        return self.root / "dashboard-mappings.json"

    @property
    def monitoring(self) -> MonitoringRuntimeLayout:
        return MonitoringRuntimeLayout(self.root / "monitoring")

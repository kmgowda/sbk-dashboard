"""Prometheus discovery and endpoint-specific Grafana dashboard provisioning."""

from __future__ import annotations

import copy
import json
import re
import threading
from importlib.resources import files
from pathlib import Path

from sbk_dashboard.files import atomic_json
from sbk_dashboard.models import BenchmarkTarget

DATASOURCE_UID = "PBFA97CFB590B2093"
SBK_SELECTOR = re.compile(r"(SBK_[A-Za-z0-9_]+)(?:\{([^}]*)\})?")


class PrometheusTargetDiscovery:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, targets: list[BenchmarkTarget]) -> None:
        groups = [
            {"targets": [target.prometheus_address],
             "labels": {"sbk_endpoint_id": target.id, "sbk_metrics_path": target.metrics_path}}
            for target in targets
        ]
        with self._lock:
            atomic_json(self.path, groups)


class GrafanaDashboardProvisioner:
    def __init__(self, dashboard_directory: Path, grafana_public_url: str) -> None:
        resource = files("sbk_dashboard").joinpath("resources/grafana/dashboards/sbk-dashboard.json")
        try:
            self._canonical = json.loads(resource.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise OSError(f"Canonical SBK Grafana dashboard is unavailable: {error}") from error
        self.dashboard_directory = dashboard_directory
        self.grafana_public_url = grafana_public_url.rstrip("/")
        self._lock = threading.Lock()

    @staticmethod
    def dashboard_uid(target_id: str) -> str:
        return f"sbk-{target_id}"

    def dashboard_url(self, target_id: str) -> str:
        return f"{self.grafana_public_url}/d/{self.dashboard_uid(target_id)}/"

    def generated_dashboard(self, target: BenchmarkTarget) -> dict[str, object]:
        dashboard = copy.deepcopy(self._canonical)
        dashboard["id"] = None
        dashboard["uid"] = self.dashboard_uid(target.id)
        dashboard["title"] = f"SBK Dashboard — {target.host}:{target.port}"
        dashboard["version"] = 1
        tags = dashboard.setdefault("tags", [])
        tags.extend(["sbk-dashboard-managed", f"endpoint:{target.id}"])
        self._scope(dashboard, target.id)
        return dashboard

    def reconcile(self, targets: list[BenchmarkTarget]) -> None:
        with self._lock:
            self.dashboard_directory.mkdir(parents=True, exist_ok=True)
            expected: set[Path] = set()
            for target in targets:
                path = self.dashboard_directory / f"{self.dashboard_uid(target.id)}.json"
                expected.add(path)
                atomic_json(path, self.generated_dashboard(target))
            for path in self.dashboard_directory.glob("sbk-*.json"):
                if path not in expected:
                    path.unlink(missing_ok=True)

    def _scope(self, node: object, target_id: str) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("expr"), str):
                node["expr"] = self._scope_promql(node["expr"], target_id)
            for key, value in node.items():
                if key != "expr":
                    self._scope(value, target_id)
        elif isinstance(node, list):
            for child in node:
                self._scope(child, target_id)

    @staticmethod
    def _scope_promql(expression: str, target_id: str) -> str:
        def replacement(match: re.Match[str]) -> str:
            labels = match.group(2)
            suffix = (
                f',sbk_endpoint_id="{target_id}"'
                if labels and labels.strip()
                else f'sbk_endpoint_id="{target_id}"'
            )
            return f"{match.group(1)}{{{labels or ''}{suffix}}}"

        return SBK_SELECTOR.sub(replacement, expression)


def write_dashboard_mappings(
    path: Path, targets: list[BenchmarkTarget], provisioner: GrafanaDashboardProvisioner
) -> None:
    atomic_json(path, [
        {"targetId": target.id, "prometheusTarget": target.prometheus_address,
         "grafanaDashboardUid": provisioner.dashboard_uid(target.id),
         "grafanaDashboardUrl": provisioner.dashboard_url(target.id)}
        for target in targets
    ])

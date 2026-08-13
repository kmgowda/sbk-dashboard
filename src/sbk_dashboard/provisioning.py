"""Prometheus discovery and endpoint-specific Grafana dashboard provisioning."""

from __future__ import annotations

import copy
import json
import threading
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from sbk_dashboard.files import atomic_json
from sbk_dashboard.models import BenchmarkTarget

DATASOURCE_UID = "PBFA97CFB590B2093"
COMPARISON_DASHBOARD_UID = "sbk-comparison"
SBK_METRIC_PREFIX = "SBK_"
SBK_ENDPOINT_LABEL = "sbk_endpoint_id"


class PrometheusTargetDiscovery:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, targets: list[BenchmarkTarget]) -> None:
        groups = [
            {"targets": [target.prometheus_address],
             "labels": {
                 "sbk_endpoint_id": target.id,
                 "sbk_dashboard_name": target.name,
                 "sbk_kind": target.kind,
                 "sbk_metrics_path": target.metrics_path,
             }}
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

    def dashboard_url(self, target_id: str, browser_host: str | None = None) -> str:
        """Build a dashboard URL, optionally using the host through which the UI was reached."""
        return f"{self._browser_base_url(browser_host)}/d/{self.dashboard_uid(target_id)}/"

    def comparison_dashboard_url(self, target_ids: list[str], browser_host: str | None = None) -> str:
        query = urlencode([("var-sbk_endpoints", target_id) for target_id in target_ids])
        return f"{self._browser_base_url(browser_host)}/d/{COMPARISON_DASHBOARD_UID}/?{query}"

    def _browser_base_url(self, browser_host: str | None) -> str:
        base_url = self.grafana_public_url
        if browser_host:
            parsed = urlsplit(base_url)
            formatted_host = f"[{browser_host}]" if ":" in browser_host else browser_host
            netloc = formatted_host if parsed.port is None else f"{formatted_host}:{parsed.port}"
            base_url = urlunsplit((parsed.scheme, netloc, parsed.path, "", "")).rstrip("/")
        return base_url

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

    def generated_comparison_dashboard(self, targets: list[BenchmarkTarget]) -> dict[str, object]:
        dashboard = copy.deepcopy(self._canonical)
        dashboard["id"] = None
        dashboard["uid"] = COMPARISON_DASHBOARD_UID
        dashboard["title"] = "SBK/SBM Live Comparison"
        dashboard["version"] = 1
        tags = dashboard.setdefault("tags", [])
        tags.extend(["sbk-dashboard-managed", "comparison"])
        templating = dashboard.setdefault("templating", {})
        variables = templating.setdefault("list", [])
        options = [
            {
                "selected": False,
                "text": f"{target.name} ({target.kind}) — {target.prometheus_address}",
                "value": target.id,
            }
            for target in targets
        ]
        variables.append({
            "name": "sbk_endpoints",
            "label": "SBK/SBM endpoints",
            "type": "custom",
            "query": ",".join(target.id for target in targets),
            "options": options,
            "current": {"selected": False, "text": [], "value": []},
            "multi": True,
            "includeAll": False,
            "hide": 0,
            "skipUrlSync": False,
        })
        self._scope_comparison(dashboard)
        return dashboard

    def reconcile(self, targets: list[BenchmarkTarget]) -> None:
        with self._lock:
            self.dashboard_directory.mkdir(parents=True, exist_ok=True)
            comparison_path = self.dashboard_directory / f"{COMPARISON_DASHBOARD_UID}.json"
            atomic_json(comparison_path, self.generated_comparison_dashboard(targets))
            expected: set[Path] = {comparison_path}
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

    def _scope_comparison(self, node: object) -> None:
        if isinstance(node, dict):
            expression = node.get("expr")
            if isinstance(expression, str):
                scoped, contains_sbk_metric = self._scope_promql_parts(
                    expression, "${sbk_endpoints:regex}", regex=True
                )
            else:
                scoped, contains_sbk_metric = "", False
            if contains_sbk_metric:
                node["expr"] = scoped
                legend = node.get("legendFormat")
                identity = "{{sbk_dashboard_name}} [{{sbk_kind}} · {{sbk_endpoint_id}}]"
                if not isinstance(legend, str) or legend in {"", "__auto"}:
                    node["legendFormat"] = identity
                elif not all(
                    label in legend
                    for label in (
                        "{{sbk_dashboard_name}}",
                        "{{sbk_kind}}",
                        "{{sbk_endpoint_id}}",
                    )
                ):
                    node["legendFormat"] = f"{identity} — {legend}"
            for key, value in node.items():
                if key not in {"expr", "legendFormat"}:
                    self._scope_comparison(value)
        elif isinstance(node, list):
            for child in node:
                self._scope_comparison(child)

    @staticmethod
    def _scope_promql(expression: str, target_id: str, *, regex: bool = False) -> str:
        return GrafanaDashboardProvisioner._scope_promql_parts(expression, target_id, regex)[0]

    @staticmethod
    def _scope_promql_parts(
        expression: str, target_id: str, regex: bool = False
    ) -> tuple[str, bool]:
        """Scope SBK metric selectors while ignoring quoted strings and existing endpoint labels."""
        output: list[str] = []
        index = 0
        contains_sbk_metric = False
        while index < len(expression):
            character = expression[index]
            if character in {'"', "'", "`"}:
                end = GrafanaDashboardProvisioner._quoted_end(expression, index, character)
                output.append(expression[index:end])
                index = end
                continue
            if expression.startswith(SBK_METRIC_PREFIX, index) and (
                index == 0 or not (expression[index - 1].isalnum() or expression[index - 1] == "_")
            ):
                metric_end = index + len(SBK_METRIC_PREFIX)
                while metric_end < len(expression) and (
                    expression[metric_end].isalnum() or expression[metric_end] == "_"
                ):
                    metric_end += 1
                if metric_end > index + len(SBK_METRIC_PREFIX):
                    contains_sbk_metric = True
                    metric = expression[index:metric_end]
                    labels = ""
                    selector_end = metric_end
                    if metric_end < len(expression) and expression[metric_end] == "{":
                        selected_end = GrafanaDashboardProvisioner._selector_end(expression, metric_end)
                        if selected_end is None:
                            output.append(metric)
                            index = metric_end
                            continue
                        selector_end = selected_end
                        labels = expression[metric_end + 1 : selector_end - 1]
                    if GrafanaDashboardProvisioner._has_endpoint_label(labels):
                        output.append(expression[index:selector_end])
                    else:
                        operator = "=~" if regex else "="
                        separator = "," if labels.strip() else ""
                        output.append(
                            f'{metric}{{{labels}{separator}{SBK_ENDPOINT_LABEL}{operator}"{target_id}"}}'
                        )
                    index = selector_end
                    continue
            output.append(character)
            index += 1
        return "".join(output), contains_sbk_metric

    @staticmethod
    def _quoted_end(expression: str, start: int, quote: str) -> int:
        index = start + 1
        while index < len(expression):
            if quote != "`" and expression[index] == "\\":
                index += 2
                continue
            index += 1
            if expression[index - 1] == quote:
                break
        return index

    @staticmethod
    def _selector_end(expression: str, start: int) -> int | None:
        index = start + 1
        while index < len(expression):
            if expression[index] in {'"', "'", "`"}:
                index = GrafanaDashboardProvisioner._quoted_end(
                    expression, index, expression[index]
                )
                continue
            if expression[index] == "}":
                return index + 1
            index += 1
        return None

    @staticmethod
    def _has_endpoint_label(labels: str) -> bool:
        index = 0
        while index < len(labels):
            while index < len(labels) and (labels[index].isspace() or labels[index] == ","):
                index += 1
            name_start = index
            while index < len(labels) and (labels[index].isalnum() or labels[index] == "_"):
                index += 1
            name = labels[name_start:index]
            while index < len(labels) and labels[index].isspace():
                index += 1
            if name == SBK_ENDPOINT_LABEL and any(
                labels.startswith(operator, index) for operator in ("=~", "!~", "!=", "=")
            ):
                return True
            while index < len(labels) and labels[index] != ",":
                if labels[index] in {'"', "'", "`"}:
                    index = GrafanaDashboardProvisioner._quoted_end(
                        labels, index, labels[index]
                    )
                else:
                    index += 1
        return False


def write_dashboard_mappings(
    path: Path, targets: list[BenchmarkTarget], provisioner: GrafanaDashboardProvisioner
) -> None:
    atomic_json(path, [
        {"targetId": target.id, "prometheusTarget": target.prometheus_address,
         "grafanaDashboardUid": provisioner.dashboard_uid(target.id),
         "grafanaDashboardUrl": provisioner.dashboard_url(target.id)}
        for target in targets
    ])

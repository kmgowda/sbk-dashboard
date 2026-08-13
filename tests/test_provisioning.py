import json
import tempfile
import unittest
from pathlib import Path

from sbk_dashboard.models import BenchmarkTarget
from sbk_dashboard.provisioning import GrafanaDashboardProvisioner, PrometheusTargetDiscovery


def target(identifier: str, port: int = 9718) -> BenchmarkTarget:
    return BenchmarkTarget(identifier, identifier, "bench.example", port, "/metrics", "SBK", "2026-01-01T00:00:00Z")


def expressions(node):
    result = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "expr" and isinstance(value, str):
                result.append(value)
            result.extend(expressions(value))
    elif isinstance(node, list):
        for value in node:
            result.extend(expressions(value))
    return result


class ProvisioningTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.provisioner = GrafanaDashboardProvisioner(self.directory / "dashboards", "http://grafana:3000/")

    def tearDown(self):
        self.temporary.cleanup()

    def test_preserves_canonical_dashboard_and_scopes_all_sbk_expressions(self):
        generated = self.provisioner.generated_dashboard(target("abc123"))
        values = expressions(generated)
        self.assertEqual("sbk-abc123", generated["uid"])
        self.assertEqual("SBK Dashboard — bench.example:9718", generated["title"])
        self.assertGreater(len(values), 0)
        self.assertTrue(all('sbk_endpoint_id="abc123"' in value for value in values if "SBK_" in value))
        self.assertEqual(53, sum(1 for _ in _panels(generated)))

    def test_reconcile_creates_distinct_dashboards_and_removes_orphans(self):
        first, second = target("first"), target("second", 9719)
        self.provisioner.reconcile([first, second])
        self.assertTrue((self.directory / "dashboards/sbk-first.json").is_file())
        self.assertTrue((self.directory / "dashboards/sbk-second.json").is_file())
        self.assertTrue((self.directory / "dashboards/sbk-comparison.json").is_file())
        self.assertEqual("http://grafana:3000/d/sbk-first/", self.provisioner.dashboard_url("first"))
        self.assertEqual(
            "http://203.0.113.8:3000/d/sbk-first/", self.provisioner.dashboard_url("first", "203.0.113.8")
        )
        self.assertEqual(
            "http://[2001:db8::8]:3000/d/sbk-first/", self.provisioner.dashboard_url("first", "2001:db8::8")
        )
        self.provisioner.reconcile([second])
        self.assertFalse((self.directory / "dashboards/sbk-first.json").exists())
        self.assertTrue((self.directory / "dashboards/sbk-comparison.json").is_file())

    def test_comparison_dashboard_scopes_every_selector_and_names_each_series(self):
        first = target("first")
        second = BenchmarkTarget(
            "second", "SBM two", "bench.example", 9719, "/metrics", "SBM", "2026-01-01T00:00:00Z"
        )
        generated = self.provisioner.generated_comparison_dashboard([first, second])
        values = expressions(generated)
        self.assertEqual("sbk-comparison", generated["uid"])
        self.assertEqual("SBK/SBM Live Comparison", generated["title"])
        self.assertTrue(all(
            'sbk_endpoint_id=~"${sbk_endpoints:regex}"' in value
            for value in values if "SBK_" in value
        ))
        variable = generated["templating"]["list"][-1]
        self.assertEqual("sbk_endpoints", variable["name"])
        self.assertTrue(variable["multi"])
        self.assertFalse(variable["includeAll"])
        self.assertEqual(["first", "second"], [option["value"] for option in variable["options"]])
        self.assertIn("SBM two (SBM)", variable["options"][1]["text"])
        legends = _values_for_key(generated, "legendFormat")
        self.assertTrue(all("{{sbk_endpoint_id}}" in legend for legend in legends))
        self.assertEqual(53, sum(1 for _ in _panels(generated)))
        self.assertEqual(
            "http://grafana:3000/d/sbk-comparison/?var-sbk_endpoints=first&var-sbk_endpoints=second",
            self.provisioner.comparison_dashboard_url(["first", "second"]),
        )

    def test_prometheus_discovery_has_endpoint_labels_and_metrics_path(self):
        path = self.directory / "prometheus/targets.json"
        PrometheusTargetDiscovery(path).write([target("first")])
        value = json.loads(path.read_text())
        self.assertEqual(["bench.example:9718"], value[0]["targets"])
        self.assertEqual("first", value[0]["labels"]["sbk_endpoint_id"])
        self.assertEqual("/metrics", value[0]["labels"]["sbk_metrics_path"])


def _panels(node):
    if isinstance(node, dict):
        if "type" in node and "title" in node:
            yield node
        for value in node.values():
            yield from _panels(value)
    elif isinstance(node, list):
        for value in node:
            yield from _panels(value)


def _values_for_key(node, selected_key):
    result = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == selected_key and isinstance(value, str):
                result.append(value)
            result.extend(_values_for_key(value, selected_key))
    elif isinstance(node, list):
        for value in node:
            result.extend(_values_for_key(value, selected_key))
    return result


if __name__ == "__main__":
    unittest.main()

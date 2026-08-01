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
        self.assertEqual("http://grafana:3000/d/sbk-first/", self.provisioner.dashboard_url("first"))
        self.assertEqual(
            "http://203.0.113.8:3000/d/sbk-first/", self.provisioner.dashboard_url("first", "203.0.113.8")
        )
        self.assertEqual(
            "http://[2001:db8::8]:3000/d/sbk-first/", self.provisioner.dashboard_url("first", "2001:db8::8")
        )
        self.provisioner.reconcile([second])
        self.assertFalse((self.directory / "dashboards/sbk-first.json").exists())

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


if __name__ == "__main__":
    unittest.main()

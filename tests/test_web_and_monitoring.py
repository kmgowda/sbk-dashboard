import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sbk_dashboard.config import DashboardConfig, MonitoringConfig
from sbk_dashboard.models import TargetStatus
from sbk_dashboard.monitoring import ManagedMonitoringStack, PortProcessManager
from sbk_dashboard.registry import TargetRegistry
from sbk_dashboard.web import DashboardHttpServer


class FakeMonitoring:
    def __init__(self):
        self.targets = []

    def healthy(self):
        return True

    def reconcile(self, targets):
        self.targets = list(targets)

    def status(self, target_id):
        return TargetStatus("up", "2026-01-01T00:00:00Z", "Prometheus target up")

    def dashboard_url(self, target_id):
        return f"http://grafana:3000/d/sbk-{target_id}/"


class WebTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.registry = TargetRegistry(Path(self.temporary.name))
        self.monitoring = FakeMonitoring()
        self.server = DashboardHttpServer(0, self.registry, self.monitoring)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server._server.server_port}"

    def tearDown(self):
        self.server.close()
        self.temporary.cleanup()

    def test_ui_health_registration_dashboard_and_deletion(self):
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertIn(b"SBK Dashboard", response.read())
        with urllib.request.urlopen(self.base + "/api/health") as response:
            self.assertEqual("ok", json.load(response)["status"])
        request = urllib.request.Request(self.base + "/api/targets", method="POST", data=json.dumps({
            "name": "Run", "host": "127.0.0.1", "port": 9718, "metricsPath": "/metrics",
        }).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            created = json.load(response)
            self.assertEqual(201, response.status)
            self.assertIn("dashboardUrl", created)
        with urllib.request.urlopen(self.base + "/api/targets") as response:
            self.assertEqual(1, len(json.load(response)))
        with urllib.request.urlopen(self.base + f"/api/targets/{created['id']}/dashboard") as response:
            self.assertIn(created["id"], json.load(response)["dashboardUrl"])
        delete = urllib.request.Request(self.base + f"/api/targets/{created['id']}", method="DELETE")
        with urllib.request.urlopen(delete) as response:
            self.assertEqual(204, response.status)

    def test_rejects_wrong_method_and_unknown_asset(self):
        for request, status in ((urllib.request.Request(self.base + "/api/health", method="POST"), 405),
                                (urllib.request.Request(self.base + "/missing"), 404)):
            with self.subTest(status=status), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(status, caught.exception.code)


class MonitoringContinueTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.prometheus = self._service({"/-/ready": b"ready", "/api/v1/targets": b'{"data":{"activeTargets":[]}}'})
        self.grafana = self._service({"/api/health": b'{"database":"ok"}'})

    def tearDown(self):
        self.prometheus.shutdown()
        self.prometheus.server_close()
        self.grafana.shutdown()
        self.grafana.server_close()
        self.temporary.cleanup()

    def test_continue_attaches_without_stopping_servers_and_generates_configuration(self):
        data = Path(self.temporary.name)
        dashboard = DashboardConfig(9721, False, True, data, 5, 7, {})
        monitoring = MonitoringConfig(Path("unused"), data / "unused", self.prometheus.server_port,
                                      self.grafana.server_port, f"http://localhost:{self.grafana.server_port}", {})
        stack = ManagedMonitoringStack(dashboard, monitoring, [])
        try:
            self.assertTrue(stack.healthy())
            self.assertIn("retention", "retention")
            config = (data / "monitoring/prometheus/prometheus.yml").read_text()
            self.assertIn("fallback_scrape_protocol: PrometheusText0.0.4", config)
        finally:
            stack.close()
        self.assertFalse(PortProcessManager.available(self.prometheus.server_port))
        self.assertFalse(PortProcessManager.available(self.grafana.server_port))

    @staticmethod
    def _service(routes):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = routes.get(self.path.split("?", 1)[0])
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


if __name__ == "__main__":
    unittest.main()

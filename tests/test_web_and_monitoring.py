import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sbk_dashboard.config import DashboardConfig, MonitoringConfig
from sbk_dashboard.models import TargetStatus
from sbk_dashboard.monitoring import ManagedMonitoringStack
from sbk_dashboard.processes import LifecycleState, PortProcessManager
from sbk_dashboard.registry import TargetRegistry
from sbk_dashboard.web import MAX_REQUEST_BYTES, DashboardHttpServer


class FakeMonitoring:
    def __init__(self, data):
        self.targets = []
        self.reconcile_error = None
        self.dashboard = DashboardConfig(9721, False, False, data, 5, 7, {})

    def healthy(self):
        return True

    def reconcile(self, targets):
        if self.reconcile_error is not None:
            raise self.reconcile_error
        self.targets = list(targets)

    def status(self, target_id):
        return TargetStatus("up", "2026-01-01T00:00:00Z", "Prometheus target up")

    def dashboard_url(self, target_id, browser_host=None):
        host = browser_host or "grafana"
        formatted = f"[{host}]" if ":" in host else host
        return f"http://{formatted}:3000/d/sbk-{target_id}/"


class WebTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.registry = TargetRegistry(Path(self.temporary.name))
        self.monitoring = FakeMonitoring(Path(self.temporary.name))
        self.server = DashboardHttpServer(0, self.registry, self.monitoring)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server._server.server_port}"

    def tearDown(self):
        self.server.close()
        self.temporary.cleanup()

    def test_ui_health_registration_dashboard_and_deletion(self):
        with urllib.request.urlopen(self.base + "/") as response:
            page = response.read()
            self.assertIn(b'value="SBK Dashboard"', page)
            self.assertIn(b'value="127.0.0.1"', page)
            self.assertNotIn(b"NVMe endurance run", page)
        with urllib.request.urlopen(self.base + "/app.js") as response:
            self.assertIn(b"form.reset();", response.read())
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
        for host, expected in (
            ("203.0.113.25:9721", "http://203.0.113.25:3000/"),
            ("dashboard.example:9721", "http://dashboard.example:3000/"),
            ("[2001:db8::25]:9721", "http://[2001:db8::25]:3000/"),
        ):
            with self.subTest(host=host):
                public_request = urllib.request.Request(self.base + "/api/targets", headers={"Host": host})
                with urllib.request.urlopen(public_request) as response:
                    self.assertTrue(json.load(response)[0]["dashboardUrl"].startswith(expected))
        malformed_request = urllib.request.Request(
            self.base + "/api/targets", headers={"Host": "127.000.000.001:9721"}
        )
        with urllib.request.urlopen(malformed_request) as response:
            self.assertTrue(json.load(response)[0]["dashboardUrl"].startswith("http://grafana:3000/"))
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

    def test_negative_content_length_cannot_bypass_request_limit(self):
        body = json.dumps({"host": "127.0.0.1", "port": 9718}).encode()
        body += b" " * (MAX_REQUEST_BYTES + 1 - len(body))
        request = (
            b"POST /api/targets HTTP/1.0\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: -1\r\n\r\n"
            + body
        )
        with socket.create_connection(("127.0.0.1", self.server._server.server_port), timeout=2) as connection:
            connection.sendall(request)
            connection.shutdown(socket.SHUT_WR)
            response = connection.recv(4096)
        self.assertIn(b"400 Bad Request", response)
        self.assertEqual([], self.registry.list())

    def test_non_string_fields_are_rejected_as_bad_request(self):
        for payload in (
            {"name": 123, "host": "127.0.0.1", "port": 9718, "metricsPath": "/metrics"},
            {"name": "x", "host": 123, "port": 9718, "metricsPath": "/metrics"},
            {"name": "x", "host": "127.0.0.1", "port": 9718, "metricsPath": 123},
        ):
            request = urllib.request.Request(
                self.base + "/api/targets",
                method="POST",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.subTest(payload=payload), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(400, caught.exception.code)
            self.assertEqual([], self.registry.list())

    def test_boolean_port_is_rejected_at_http_boundary(self):
        request = urllib.request.Request(
            self.base + "/api/targets",
            method="POST",
            data=json.dumps({"host": "127.0.0.1", "port": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(400, caught.exception.code)
        self.assertEqual([], self.registry.list())

    def test_registration_is_rolled_back_for_runtime_reconciliation_failure(self):
        self.monitoring.reconcile_error = RuntimeError("monitoring stopped")
        request = urllib.request.Request(
            self.base + "/api/targets",
            method="POST",
            data=json.dumps({"host": "127.0.0.1", "port": 9718}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(500, caught.exception.code)
        self.assertEqual([], self.registry.list())
        self.assertEqual([], TargetRegistry(Path(self.temporary.name)).list())

    def test_deletion_is_rolled_back_for_runtime_reconciliation_failure(self):
        target = self.registry.register("Run", "127.0.0.1", 9718, "/metrics")
        self.monitoring.reconcile_error = RuntimeError("monitoring stopped")
        request = urllib.request.Request(self.base + f"/api/targets/{target.id}", method="DELETE")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(500, caught.exception.code)
        self.assertEqual(target, self.registry.find(target.id))
        self.assertEqual(target, TargetRegistry(Path(self.temporary.name)).find(target.id))


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
        stack = ManagedMonitoringStack(dashboard, monitoring)
        try:
            stack.start([])
            self.assertTrue(stack.healthy())
            self.assertEqual(LifecycleState.RUNNING, stack.state)
            config = (data / "monitoring/prometheus/prometheus.yml").read_text()
            self.assertIn("fallback_scrape_protocol: PrometheusText0.0.4", config)
            self.assertIn("http_addr = 0.0.0.0", (data / "monitoring/grafana/grafana.ini").read_text())
            self.assertEqual(45, stack._services[0].spec.startup_timeout_seconds)
            self.assertEqual(120, stack._services[1].spec.startup_timeout_seconds)
        finally:
            stack.close()
        self.assertEqual(LifecycleState.STOPPED, stack.state)
        self.assertFalse(PortProcessManager.available(self.prometheus.server_port, "127.0.0.1"))
        self.assertFalse(PortProcessManager.available(self.grafana.server_port, "127.0.0.1"))

    def test_default_grafana_url_follows_browser_host_but_explicit_url_is_authoritative(self):
        data = Path(self.temporary.name)
        dashboard = DashboardConfig(9721, False, False, data, 5, 7, {})
        default = MonitoringConfig(
            Path("unused"), data / "unused", 19090, 3000, "http://localhost:3000", {"grafana-url": "default"}
        )
        explicit = MonitoringConfig(
            Path("unused"), data / "unused", 19090, 3000, "https://grafana.example/base",
            {"grafana-url": "command line"},
        )
        self.assertEqual(
            "http://198.51.100.7:3000/d/sbk-target/",
            ManagedMonitoringStack(dashboard, default).dashboard_url("target", "198.51.100.7"),
        )
        self.assertEqual(
            "https://grafana.example/base/d/sbk-target/",
            ManagedMonitoringStack(dashboard, explicit).dashboard_url("target", "198.51.100.7"),
        )

    def test_default_prometheus_command_binds_only_to_loopback(self):
        data = Path(self.temporary.name)
        binary = data / "prometheus"
        binary.write_text("binary", encoding="utf-8")
        binary.chmod(0o755)
        dashboard = DashboardConfig(9721, False, False, data, 5, 7, {})
        monitoring = MonitoringConfig(binary, data / "unused", 19090, 3000, "http://localhost:3000", {})
        command = ManagedMonitoringStack(dashboard, monitoring)._prometheus_command()
        self.assertIn("--web.listen-address=127.0.0.1:19090", command)

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

import contextlib
import hashlib
import io
import json
import re
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sbk_dashboard.config import DashboardConfig, MonitoringConfig, RuntimePlatform
from sbk_dashboard.models import BenchmarkTarget, TargetStatus
from sbk_dashboard.monitoring import ManagedMonitoringStack
from sbk_dashboard.processes import LifecycleState, PortProcessManager
from sbk_dashboard.registry import TargetRegistry
from sbk_dashboard.web import MAX_REQUEST_BYTES, DashboardHttpServer, RecentClientActivity


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

    def comparison_dashboard_url(self, target_ids, browser_host=None):
        host = browser_host or "grafana"
        formatted = f"[{host}]" if ":" in host else host
        normalized = sorted(set(target_ids))
        digest = hashlib.sha256("\n".join(normalized).encode()).hexdigest()[:16]
        query = "&".join(f"var-sbk_endpoints={target_id}" for target_id in normalized)
        return f"http://{formatted}:3000/d/sbk-comparison-{digest}/?{query}"

    def comparison_dashboard_id(self, target_ids):
        normalized = sorted(set(target_ids))
        digest = hashlib.sha256("\n".join(normalized).encode()).hexdigest()[:16]
        return f"sbk-comparison-{digest}"


class AssetRenderingTest(unittest.TestCase):
    def test_index_renders_validated_native_and_container_defaults(self):
        for host in ("127.0.0.1", "host.docker.internal"):
            with self.subTest(host=host):
                server = object.__new__(DashboardHttpServer)
                server._default_target_host = host
                request = SimpleNamespace(
                    command="GET",
                    wfile=io.BytesIO(),
                    send_response=lambda _status: None,
                    send_header=lambda _name, _value: None,
                    end_headers=lambda: None,
                )
                server._asset(request, "/")
                page = request.wfile.getvalue()
                self.assertIn(f'value="{host}"'.encode(), page)
                self.assertNotIn(b"__DEFAULT_TARGET_HOST__", page)


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
            self.assertIn(b"<title>SBK DASHBOARD</title>", page)
            self.assertIn(b"<h1>SBK DASHBOARD</h1>", page)
            self.assertNotIn(b"performance control plane", page.lower())
            self.assertIn(b'value="SBK Dashboard"', page)
            self.assertIn(b'value="127.0.0.1"', page)
            self.assertIn(b'id="target-count"', page)
            self.assertIn(b'id="up-count"', page)
            self.assertIn(b'id="down-count"', page)
            self.assertIn(b'id="compare-selected"', page)
            self.assertIn(b'<option value="SBM">SBM</option>', page)
            self.assertIn(b'aria-label="Endpoint status summary" aria-live="polite"', page)
            self.assertNotIn(b"NVMe endurance run", page)
            self.assertNotIn(b"__ASSET_VERSION__", page)
            self.assertNotIn(b"__DEFAULT_TARGET_HOST__", page)
            versions = re.findall(rb'(?:app\.css|app\.js)\?v=([0-9a-f]{12})', page)
            self.assertEqual(2, len(versions))
            self.assertEqual(versions[0], versions[1])
        with urllib.request.urlopen(self.base + "/app.js") as response:
            self.assertEqual("no-cache", response.headers["Cache-Control"])
            script = response.read()
            self.assertIn(b"form.reset();", script)
            self.assertIn(b"updateEndpointSummary(targets);", script)
            self.assertIn(b"target.status.state === 'up'", script)
            self.assertIn(b"target.status.state === 'down'", script)
            self.assertIn(b"reportActivity('landing');", script)
            self.assertIn(b"reportActivity('grafana')", script)
            self.assertIn(b"window.sessionStorage", script)
            self.assertIn(b"/api/comparison-dashboard", script)
            self.assertIn(b"const MAX_COMPARISON_TARGETS = 8;", script)
            self.assertNotIn(b"__MAX_COMPARISON_TARGETS__", script)
        with urllib.request.urlopen(self.base + "/app.css") as response:
            self.assertEqual("no-cache", response.headers["Cache-Control"])
            stylesheet = response.read()
            self.assertIn(b"font-size: clamp(34px, 4.5vw, 54px);", stylesheet)
            self.assertIn(b".hero-stat { width: min(100%, 420px); }", stylesheet)
            self.assertNotIn(b".hero-stat { display: none; }", stylesheet)
        with urllib.request.urlopen(self.base + "/api/health") as response:
            self.assertEqual("ok", json.load(response)["status"])
        for surface in ("landing", "grafana"):
            activity = urllib.request.Request(
                self.base + f"/api/activity/{surface}",
                method="POST",
                data=json.dumps({"clientId": "browser-client-0001"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(activity) as response:
                self.assertEqual(204, response.status)
                self.assertEqual("no-store", response.headers["Cache-Control"])
        clients = self.server.client_activity()
        self.assertEqual((1, 1, 1), (clients.total, clients.landing, clients.grafana_opens))
        request = urllib.request.Request(self.base + "/api/targets", method="POST", data=json.dumps({
            "name": "Run", "host": "127.0.0.1", "port": 9718, "metricsPath": "/metrics",
        }).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            created = json.load(response)
            self.assertEqual(201, response.status)
            self.assertIn("dashboardUrl", created)
            self.assertEqual("SBK", created["kind"])
        repeated_request = urllib.request.Request(
            self.base + "/api/targets", method="POST", data=json.dumps({
                "name": " Run ", "kind": "sbk", "host": "127.0.0.1", "port": 9718,
                "metricsPath": "/metrics",
            }).encode(), headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(repeated_request) as response:
            repeated = json.load(response)
            self.assertEqual(200, response.status)
        self.assertEqual(created, repeated)
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

    def test_registers_sbm_and_builds_bounded_comparison_url(self):
        target_ids = []
        for port, kind in ((9718, "SBK"), (9719, "SBM")):
            request = urllib.request.Request(
                self.base + "/api/targets",
                method="POST",
                data=json.dumps({
                    "name": f"{kind} run", "host": "127.0.0.1", "port": port,
                    "metricsPath": "/metrics", "kind": kind,
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                target_ids.append(json.load(response)["id"])
        comparison = urllib.request.Request(
            self.base + "/api/comparison-dashboard",
            method="POST",
            data=json.dumps({"targetIds": target_ids}).encode(),
            headers={"Content-Type": "application/json", "Host": "dashboard.example:9721"},
        )
        with urllib.request.urlopen(comparison) as response:
            body = json.load(response)
        self.assertTrue(body["dashboardId"].startswith("sbk-comparison-"))
        self.assertTrue(
            body["dashboardUrl"].startswith(
                f"http://dashboard.example:3000/d/{body['dashboardId']}/"
            )
        )
        self.assertEqual(2, body["dashboardUrl"].count("var-sbk_endpoints="))
        repeated = urllib.request.Request(
            self.base + "/api/comparison-dashboard",
            method="POST",
            data=json.dumps({"targetIds": list(reversed(target_ids))}).encode(),
            headers={"Content-Type": "application/json", "Host": "dashboard.example:9721"},
        )
        with urllib.request.urlopen(repeated) as response:
            repeated_body = json.load(response)
        self.assertEqual(body, repeated_body)
        self.assertEqual({"SBK", "SBM"}, {target.kind for target in self.registry.list()})

    def test_rejects_invalid_comparison_selections(self):
        first = self.registry.register("One", "host", 9718, "/metrics")
        payloads = (
            {},
            {"targetIds": [first.id]},
            {"targetIds": [first.id, first.id]},
            {"targetIds": [first.id, "missing"]},
            {"targetIds": [str(index) for index in range(9)]},
            {"targetIds": [first.id, 123]},
        )
        for payload in payloads:
            request = urllib.request.Request(
                self.base + "/api/comparison-dashboard",
                method="POST",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.subTest(payload=payload), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(400, caught.exception.code)

    def test_ui_uses_configured_container_default_target_host(self):
        self.server.close()
        self.monitoring.dashboard = DashboardConfig(
            9721,
            False,
            False,
            Path(self.temporary.name),
            5,
            7,
            {},
            default_target_host="host.docker.internal",
        )
        self.server = DashboardHttpServer(0, self.registry, self.monitoring)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server._server.server_port}"
        with urllib.request.urlopen(self.base + "/") as response:
            page = response.read()
        self.assertIn(b'value="host.docker.internal"', page)
        self.assertNotIn(b'value="127.0.0.1"', page)

    def test_rejects_wrong_method_and_unknown_asset(self):
        for request, status in ((urllib.request.Request(self.base + "/api/health", method="POST"), 405),
                                (urllib.request.Request(self.base + "/missing"), 404)):
            with self.subTest(status=status), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(status, caught.exception.code)

    def test_rejects_invalid_client_activity(self):
        requests = (
            urllib.request.Request(self.base + "/api/activity/landing", method="GET"),
            urllib.request.Request(
                self.base + "/api/activity/unknown",
                method="POST",
                data=json.dumps({"clientId": "browser-client-0001"}).encode(),
                headers={"Content-Type": "application/json"},
            ),
            urllib.request.Request(
                self.base + "/api/activity/landing",
                method="POST",
                data=json.dumps({"clientId": "short"}).encode(),
                headers={"Content-Type": "application/json"},
            ),
        )
        for request, expected in zip(requests, (405, 400, 400), strict=True):
            with self.subTest(expected=expected), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(expected, caught.exception.code)

    def test_recent_client_activity_is_bounded_and_expires(self):
        activity = RecentClientActivity(capacity=2)
        activity.record("landing", "browser-client-0001", now=0)
        activity.record("landing", "browser-client-0002", now=1)
        activity.record("landing", "browser-client-0003", now=2)
        activity.record("grafana", "browser-client-0002", now=2)
        summary = activity.summary(now=2)
        self.assertEqual((2, 2, 1), (summary.total, summary.landing, summary.grafana_opens))

        summary = activity.summary(now=121.5)
        self.assertEqual((2, 1, 1), (summary.total, summary.landing, summary.grafana_opens))
        summary = activity.summary(now=303)
        self.assertEqual((0, 0, 0), (summary.total, summary.landing, summary.grafana_opens))
        with self.assertRaisesRegex(ValueError, "positive"):
            RecentClientActivity(capacity=0)

    def test_recent_client_activity_remains_bounded_under_concurrency(self):
        activity = RecentClientActivity(capacity=10)
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(
                executor.map(
                    lambda index: activity.record("landing", f"browser-client-{index:04d}"),
                    range(100),
                )
            )
        summary = activity.summary()
        self.assertEqual((10, 10, 0), (summary.total, summary.landing, summary.grafana_opens))

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
            # The server may reject the invalid Content-Length and close first.
            # macOS reports ENOTCONN from shutdown in that valid peer-close race.
            with contextlib.suppress(OSError):
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

    def test_http_server_rejects_close_from_its_worker_pool(self):
        future = self.server._server._executor.submit(self.server.close)
        with self.assertRaisesRegex(RuntimeError, "must not be called from an HTTP worker"):
            future.result(2)

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
        self.prometheus_routes = {
            "/-/ready": b"ready",
            "/api/v1/targets": b'{"data":{"activeTargets":[]}}',
        }
        self.prometheus = self._service(self.prometheus_routes)
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
        first = BenchmarkTarget("one", "One", "bench.example", 9718, "/metrics", "SBK", "now")
        second = BenchmarkTarget("two", "Two", "bench.example", 9719, "/metrics", "SBM", "now")
        default_stack = ManagedMonitoringStack(dashboard, default)
        explicit_stack = ManagedMonitoringStack(dashboard, explicit)
        default_stack._targets = (first, second)
        explicit_stack._targets = (first, second)
        comparison_uid = default_stack.dashboard_provisioner.comparison_dashboard_uid(["one", "two"])
        self.assertEqual(
            "http://198.51.100.7:3000/d/sbk-target/",
            ManagedMonitoringStack(dashboard, default).dashboard_url("target", "198.51.100.7"),
        )
        self.assertEqual(
            "https://grafana.example/base/d/sbk-target/",
            ManagedMonitoringStack(dashboard, explicit).dashboard_url("target", "198.51.100.7"),
        )
        self.assertEqual(
            f"http://198.51.100.7:3000/d/{comparison_uid}/?var-sbk_endpoints=one&var-sbk_endpoints=two",
            default_stack.comparison_dashboard_url(["one", "two"], "198.51.100.7"),
        )
        self.assertEqual(
            f"https://grafana.example/base/d/{comparison_uid}/?var-sbk_endpoints=one&var-sbk_endpoints=two",
            explicit_stack.comparison_dashboard_url(["one", "two"], "198.51.100.7"),
        )

    def test_registered_target_missing_from_prometheus_is_down_and_can_recover(self):
        data = Path(self.temporary.name)
        dashboard = DashboardConfig(9721, False, True, data, 5, 7, {})
        monitoring = MonitoringConfig(
            Path("unused"),
            data / "unused",
            self.prometheus.server_port,
            self.grafana.server_port,
            f"http://localhost:{self.grafana.server_port}",
            {},
        )
        stack = ManagedMonitoringStack(dashboard, monitoring)
        target = BenchmarkTarget(
            "old-session", "Old session", "127.0.0.1", 9718, "/metrics", "SBK", "now"
        )
        stack.reconcile([target])
        self.assertEqual("pending", stack.status(target.id).state)

        stack.refresh_statuses()
        self.assertEqual("down", stack.status(target.id).state)
        self.assertIn("does not report", stack.status(target.id).detail)
        self.assertEqual((1, 0, 1, 0, 0), _status_counts(stack))

        self.prometheus_routes["/api/v1/targets"] = json.dumps(
            {
                "data": {
                    "activeTargets": [
                        {
                            "labels": {"sbk_endpoint_id": target.id},
                            "health": "up",
                            "lastScrape": "2026-08-03T10:00:00Z",
                            "lastError": "",
                        }
                    ]
                }
            }
        ).encode()
        stack.refresh_statuses()
        self.assertEqual("up", stack.status(target.id).state)
        self.assertEqual((1, 1, 0, 0, 0), _status_counts(stack))

    def test_refresh_does_not_publish_across_reconciliation_generation(self):
        data = Path(self.temporary.name)
        dashboard = DashboardConfig(9721, False, True, data, 5, 7, {})
        monitoring = MonitoringConfig(
            Path("unused"), data / "unused", self.prometheus.server_port,
            self.grafana.server_port, f"http://localhost:{self.grafana.server_port}", {},
        )
        stack = ManagedMonitoringStack(dashboard, monitoring)
        old = BenchmarkTarget("old", "Old", "127.0.0.1", 9718, "/metrics", "SBK", "now")
        new = BenchmarkTarget("new", "New", "127.0.0.1", 9719, "/metrics", "SBK", "now")
        stack.reconcile([old])
        entered = threading.Event()
        release = threading.Event()
        body = json.dumps({
            "data": {"activeTargets": [{
                "labels": {"sbk_endpoint_id": old.id}, "health": "up", "lastScrape": "now",
            }]}
        }).encode()

        class DelayedResponse:
            status = 200
            headers = {"Content-Length": str(len(body))}

            def __enter__(self):
                entered.set()
                self.assert_released = release.wait(2)
                return self

            def __exit__(self, *_):
                return False

            def read(self, _limit):
                return body

        with mock.patch("sbk_dashboard.monitoring.urllib.request.urlopen", return_value=DelayedResponse()):
            refresh = threading.Thread(target=stack.refresh_statuses)
            refresh.start()
            self.assertTrue(entered.wait(1))
            stack.reconcile([new])
            release.set()
            refresh.join(2)

        self.assertFalse(refresh.is_alive())
        self.assertEqual("pending", stack.status(new.id).state)
        self.assertEqual("pending", stack.status(old.id).state)
        self.assertNotIn(old.id, stack._statuses)
        self.assertEqual((1, 0, 0, 1, 0), _status_counts(stack))

    def test_default_prometheus_command_binds_only_to_loopback(self):
        data = Path(self.temporary.name)
        binary = data / "prometheus"
        binary.write_text("binary", encoding="utf-8")
        binary.chmod(0o755)
        dashboard = DashboardConfig(9721, False, False, data, 5, 7, {})
        monitoring = MonitoringConfig(binary, data / "unused", 19090, 3000, "http://localhost:3000", {})
        command = ManagedMonitoringStack(dashboard, monitoring)._prometheus_command()
        self.assertIn("--web.listen-address=127.0.0.1:19090", command)
        self.assertIn("--storage.tsdb.retention.time=7d", command)

    def test_stack_never_replaces_operator_supplied_native_ports(self):
        data = Path(self.temporary.name)
        dashboard = DashboardConfig(9721, False, False, data, 5, 7, {})
        monitoring = MonitoringConfig(
            Path("prometheus"),
            data / "grafana",
            19090,
            13000,
            "http://localhost:13000",
            {
                "prometheus-port": "command line",
                "grafana-port": "environment SBK_DASHBOARD_GRAFANA_PORT",
            },
        )
        stack = ManagedMonitoringStack(dashboard, monitoring)
        with (
            mock.patch.object(stack, "_prepare_configuration"),
            mock.patch.object(stack, "reconcile"),
            mock.patch.object(stack, "_validate_prometheus_configuration"),
            mock.patch(
                "sbk_dashboard.monitoring.PortProcessManager.terminate_existing",
                side_effect=OSError("busy user port"),
            ) as terminate,
            self.assertRaisesRegex(OSError, "busy user port"),
        ):
            stack.start([])
        self.assertFalse(terminate.call_args.kwargs["replace_prometheus"])
        self.assertFalse(terminate.call_args.kwargs["replace_grafana"])

    def test_promtool_validates_generated_configuration_and_rejects_failure(self):
        data = Path(self.temporary.name)
        prometheus = data / "prometheus"
        promtool = data / "promtool"
        for executable_path in (prometheus, promtool):
            executable_path.write_text("tool", encoding="utf-8")
            executable_path.chmod(0o755)
        dashboard = DashboardConfig(9721, False, False, data, 5, 7, {})
        monitoring = MonitoringConfig(
            prometheus, data / "unused", 19090, 3000, "http://localhost:3000", {}
        )
        stack = ManagedMonitoringStack(dashboard, monitoring)
        stack._prepare_configuration()
        with (
            mock.patch(
                "sbk_dashboard.monitoring.RuntimePlatform.current",
                return_value=RuntimePlatform("linux", "x86_64"),
            ),
            mock.patch("sbk_dashboard.monitoring.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            stack._validate_prometheus_configuration()
            self.assertEqual(
                [
                    str(promtool.resolve()),
                    "check",
                    "config",
                    str(data / "monitoring/prometheus/prometheus.yml"),
                ],
                run.call_args.args[0],
            )
            run.return_value.returncode = 1
            with self.assertRaisesRegex(OSError, "promtool rejected"):
                stack._validate_prometheus_configuration()

    def test_status_summary_is_bounded_to_published_snapshots(self):
        data = Path(self.temporary.name)
        dashboard = DashboardConfig(9721, False, False, data, 5, 7, {})
        monitoring = MonitoringConfig(Path("unused"), data / "unused", 19090, 3000, "http://localhost:3000", {})
        stack = ManagedMonitoringStack(dashboard, monitoring)
        targets = tuple(
            BenchmarkTarget(str(index), f"Run {index}", "127.0.0.1", 9718 + index, "/metrics", "SBK", "now")
            for index in range(4)
        )
        stack.lifecycle.transition(LifecycleState.STARTING)
        stack.lifecycle.transition(LifecycleState.RUNNING)
        stack._services = (
            SimpleNamespace(healthy=lambda: True),
            SimpleNamespace(healthy=lambda: False),
        )
        with stack._data_lock:
            stack._targets = targets
            stack._statuses = {
                "0": TargetStatus("up"),
                "1": TargetStatus("down"),
                "2": TargetStatus("pending"),
                "3": TargetStatus("unexpected"),
            }
        summary = stack.summary()
        self.assertEqual("running", summary.stack_state)
        self.assertTrue(summary.prometheus_healthy)
        self.assertFalse(summary.grafana_healthy)
        self.assertEqual((4, 1, 1, 1, 1), (
            summary.endpoints, summary.up, summary.down, summary.pending, summary.unknown
        ))

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


def _status_counts(stack: ManagedMonitoringStack) -> tuple[int, int, int, int, int]:
    summary = stack.summary()
    return summary.endpoints, summary.up, summary.down, summary.pending, summary.unknown


if __name__ == "__main__":
    unittest.main()

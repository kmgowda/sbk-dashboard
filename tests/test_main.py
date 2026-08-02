import contextlib
import io
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sbk_dashboard.config import parse_configuration
from sbk_dashboard.main import dashboard_links, main, print_effective, print_runtime, run


class MainTest(unittest.TestCase):
    def test_help_prints_python_runtime_and_options(self):
        output = io.StringIO()
        error = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(["-h"])
        self.assertEqual(0, stopped.exception.code)
        self.assertIn("Python version:", error.getvalue())
        self.assertIn("-continue", output.getvalue())

    def test_invalid_configuration_exits_with_usage_error(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as stopped:
            main(["-auth", "true"])
        self.assertEqual(2, stopped.exception.code)
        self.assertIn("reserved for a future release", error.getvalue())

    def test_runtime_and_effective_configuration_output(self):
        configuration = parse_configuration(["-port", "19721"], {})
        with self.assertLogs("sbk_dashboard.main", level="INFO") as captured:
            print_runtime([])
            print_effective(configuration, configuration.monitoring)
        text = "\n".join(captured.output)
        self.assertIn("Supplied arguments: (none)", text)
        self.assertIn("port=19721 [command line]", text)
        self.assertIn("retention-days=7 [default]", text)

    def test_dashboard_links_always_include_loopback(self):
        links = dashboard_links(9721)
        self.assertEqual("http://localhost:9721/", links[0])
        self.assertEqual("http://127.0.0.1:9721/", links[1])
        self.assertEqual(["http://198.51.100.8:9721/"], dashboard_links(9721, "198.51.100.8"))
        self.assertEqual(["http://[::1]:9721/"], dashboard_links(9721, "::1"))

    @patch("sbk_dashboard.main.psutil.net_if_addrs")
    def test_wildcard_dashboard_links_match_bound_address_family(self, addresses):
        addresses.return_value = {
            "test": [
                SimpleNamespace(family=socket.AF_INET, address="198.51.100.8"),
                SimpleNamespace(family=socket.AF_INET6, address="2001:db8::8"),
            ]
        }
        self.assertEqual(
            ["http://localhost:9721/", "http://127.0.0.1:9721/", "http://198.51.100.8:9721/"],
            dashboard_links(9721, "0.0.0.0"),
        )
        self.assertEqual(
            ["http://[::1]:9721/", "http://[2001:db8::8]:9721/"],
            dashboard_links(9721, "::"),
        )

    @patch("sbk_dashboard.main.signal.signal", return_value=0)
    @patch("sbk_dashboard.main.threading.Event")
    @patch("sbk_dashboard.main.DashboardHttpServer")
    @patch("sbk_dashboard.main.ManagedMonitoringStack")
    @patch("sbk_dashboard.main.TargetRegistry")
    def test_run_starts_and_closes_every_component(
        self, registry_type, monitoring_type, server_type, event_type, _signal
    ):
        configuration = parse_configuration([], {})
        event_type.return_value.wait.return_value = True
        run(configuration, configuration.monitoring)
        monitoring_type.return_value.start.assert_called_once_with(registry_type.return_value.list.return_value)
        server_type.return_value.start.assert_called_once_with()
        server_type.return_value.close.assert_called_once_with()
        monitoring_type.return_value.close.assert_called_once_with()

    @patch("sbk_dashboard.main.signal.signal", return_value=0)
    @patch("sbk_dashboard.main.threading.Event")
    @patch("sbk_dashboard.main.DashboardHttpServer")
    @patch("sbk_dashboard.main.ManagedMonitoringStack")
    @patch("sbk_dashboard.main.TargetRegistry")
    def test_run_aggregates_shutdown_errors(
        self, _registry_type, monitoring_type, server_type, event_type, _signal
    ):
        configuration = parse_configuration([], {})
        event_type.return_value.wait.return_value = True
        server_type.return_value.close.side_effect = OSError("server close")
        monitoring_type.return_value.close.side_effect = OSError("monitor close")
        with self.assertRaisesRegex(OSError, "HTTP server: server close; monitoring stack: monitor close"):
            run(configuration, configuration.monitoring)

    @patch("sbk_dashboard.main.DashboardHttpServer", side_effect=OSError("constructor failed"))
    @patch("sbk_dashboard.main.ManagedMonitoringStack")
    @patch("sbk_dashboard.main.TargetRegistry")
    def test_run_cleans_monitoring_when_http_construction_fails(
        self, _registry_type, monitoring_type, _server_type
    ):
        configuration = parse_configuration([], {})
        with self.assertRaisesRegex(OSError, "constructor failed"):
            run(configuration, configuration.monitoring)
        monitoring_type.return_value.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

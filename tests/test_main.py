import contextlib
import io
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from sbk_dashboard.config import parse_configuration
from sbk_dashboard.main import (
    dashboard_links,
    log_status,
    main,
    open_landing_page,
    print_effective,
    print_runtime,
    run,
)


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
        self.assertIn("-status-seconds", output.getvalue())
        self.assertIn("-v, --version", output.getvalue())

    def test_version_option_prints_application_version(self):
        output = io.StringIO()
        error = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
            self.assertRaises(SystemExit) as stopped,
        ):
            main(["-v"])
        self.assertEqual(0, stopped.exception.code)
        self.assertEqual("sbk-dashboard 1.26.8.1\n", output.getvalue())
        self.assertIn("SBK Dashboard version: 1.26.8.1", error.getvalue())

    def test_invalid_configuration_exits_with_usage_error(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as stopped:
            main(["-auth", "true"])
        self.assertEqual(2, stopped.exception.code)
        self.assertIn("reserved for a future release", error.getvalue())

    def test_runtime_and_effective_configuration_output(self):
        configuration = parse_configuration(
            ["-port", "19721"],
            {"SBK_DASHBOARD_HTTP_WORKERS": "12", "SBK_DASHBOARD_MONITORING_PROPERTIES": ""},
        )
        with self.assertLogs("sbk_dashboard.main", level="INFO") as captured:
            print_runtime([])
            print_effective(configuration, configuration.monitoring)
        text = "\n".join(captured.output)
        self.assertIn("/ ____|  _ \\| |/ /", text)
        self.assertIn("|_____/|____/|_|\\_\\", text)
        self.assertIn("Supplied arguments: (none)", text)
        self.assertIn("SBK Dashboard version: 1.26.8.1", text)
        self.assertIn("port=19721 [command line]", text)
        self.assertIn("retention-days=7 [default]", text)
        self.assertIn("status-seconds=60 [default]", text)
        self.assertIn("http-workers=12 [environment SBK_DASHBOARD_HTTP_WORKERS]", text)
        self.assertIn("monitoring-properties=packaged monitoring-download.properties [default]", text)

    @patch("sbk_dashboard.main.files")
    def test_runtime_continues_when_banner_cannot_be_read(self, resource_files):
        resource_files.return_value.joinpath.return_value.read_text.side_effect = OSError("missing banner")
        with self.assertLogs("sbk_dashboard.main", level="INFO") as captured:
            print_runtime([])
        text = "\n".join(captured.output)
        self.assertIn("Unable to load startup banner: missing banner", text)
        self.assertIn("SBK Dashboard version: 1.26.8.1", text)

    def test_dashboard_links_always_include_loopback(self):
        links = dashboard_links(9721)
        self.assertEqual("http://localhost:9721/", links[0])
        self.assertEqual("http://127.0.0.1:9721/", links[1])
        self.assertEqual(["http://198.51.100.8:9721/"], dashboard_links(9721, "198.51.100.8"))
        self.assertEqual(["http://[::1]:9721/"], dashboard_links(9721, "::1"))

    def test_landing_page_opens_in_new_tab_for_graphical_desktops(self):
        for platform_name, os_name, environment in (
            ("linux", "posix", {"DISPLAY": ":0"}),
            ("linux", "posix", {"WAYLAND_DISPLAY": "wayland-0"}),
            ("darwin", "posix", {}),
            ("win32", "nt", {}),
        ):
            with self.subTest(platform=platform_name, environment=environment):
                opener = MagicMock(return_value=True)
                self.assertTrue(
                    open_landing_page(
                        "http://localhost:9721/", environment, platform_name, os_name, opener
                    )
                )
                opener.assert_called_once_with("http://localhost:9721/", new=2, autoraise=True)

    def test_landing_page_does_not_open_for_ssh_ci_or_headless_sessions(self):
        for environment, platform_name, os_name in (
            ({"SSH_CONNECTION": "client server"}, "linux", "posix"),
            ({"SSH_CLIENT": "client"}, "darwin", "posix"),
            ({"SSH_TTY": "/dev/pts/1", "DISPLAY": ":10"}, "linux", "posix"),
            ({"CI": "true", "DISPLAY": ":0"}, "linux", "posix"),
            ({"SESSIONNAME": "Services"}, "win32", "nt"),
            ({}, "linux", "posix"),
        ):
            with self.subTest(environment=environment, platform=platform_name):
                opener = MagicMock(return_value=True)
                self.assertFalse(
                    open_landing_page(
                        "http://localhost:9721/", environment, platform_name, os_name, opener
                    )
                )
                opener.assert_not_called()

    def test_browser_launch_failure_is_non_fatal(self):
        opener = MagicMock(side_effect=RuntimeError("browser unavailable"))
        with self.assertLogs("sbk_dashboard.main", level="WARNING") as captured:
            self.assertFalse(
                open_landing_page(
                    "http://localhost:9721/", {"DISPLAY": ":0"}, "linux", "posix", opener
                )
            )
        self.assertIn("Unable to open landing page automatically", "\n".join(captured.output))

    def test_periodic_status_failure_is_non_fatal(self):
        monitoring = MagicMock()
        monitoring.summary.side_effect = RuntimeError("snapshot unavailable")
        with self.assertLogs("sbk_dashboard.main", level="WARNING") as captured:
            log_status(MagicMock(), monitoring)
        self.assertIn("Unable to produce periodic status: snapshot unavailable", "\n".join(captured.output))

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
    @patch("sbk_dashboard.main.open_landing_page")
    @patch("sbk_dashboard.main.DashboardHttpServer")
    @patch("sbk_dashboard.main.ManagedMonitoringStack")
    @patch("sbk_dashboard.main.TargetRegistry")
    def test_run_starts_and_closes_every_component(
        self, registry_type, monitoring_type, server_type, open_page, event_type, _signal
    ):
        configuration = parse_configuration([], {})
        event_type.return_value.wait.return_value = True
        run(configuration, configuration.monitoring)
        monitoring_type.return_value.start.assert_called_once_with(registry_type.return_value.list.return_value)
        server_type.return_value.start.assert_called_once_with()
        open_page.assert_called_once_with("http://localhost:9721/")
        server_type.return_value.close.assert_called_once_with()
        monitoring_type.return_value.close.assert_called_once_with()

    @patch("sbk_dashboard.main.signal.signal", return_value=0)
    @patch("sbk_dashboard.main.threading.Event")
    @patch("sbk_dashboard.main.open_landing_page")
    @patch("sbk_dashboard.main.DashboardHttpServer")
    @patch("sbk_dashboard.main.ManagedMonitoringStack")
    @patch("sbk_dashboard.main.TargetRegistry")
    def test_run_prints_periodic_short_status_at_configured_interval(
        self, _registry_type, monitoring_type, server_type, _open_page, event_type, _signal
    ):
        configuration = parse_configuration(["-status-seconds", "7"], {})
        event_type.return_value.wait.side_effect = [False, True]
        server_type.return_value.lifecycle.state.value = "running"
        monitoring_type.return_value.summary.return_value = SimpleNamespace(
            stack_state="running",
            prometheus_healthy=True,
            grafana_healthy=False,
            endpoints=3,
            up=1,
            down=1,
            pending=1,
            unknown=0,
        )
        server_type.return_value.client_activity.return_value = SimpleNamespace(
            total=2,
            landing=2,
            grafana_opens=1,
        )
        with self.assertLogs("sbk_dashboard.main", level="INFO") as captured:
            run(configuration, configuration.monitoring)
        self.assertEqual([call(7), call(7)], event_type.return_value.wait.call_args_list)
        self.assertIn(
            "Status: server=running stack=running prometheus=up grafana=down "
            "endpoints=3 up=1 down=1 pending=1 unknown=0 clients_recent=2 "
            "landing_clients_2m=2 grafana_opens_5m=1",
            "\n".join(captured.output),
        )

    @patch("sbk_dashboard.main.signal.signal", return_value=0)
    @patch("sbk_dashboard.main.threading.Event")
    @patch("sbk_dashboard.main.open_landing_page")
    @patch("sbk_dashboard.main.DashboardHttpServer")
    @patch("sbk_dashboard.main.ManagedMonitoringStack")
    @patch("sbk_dashboard.main.TargetRegistry")
    def test_run_aggregates_shutdown_errors(
        self, _registry_type, monitoring_type, server_type, _open_page, event_type, _signal
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

import tempfile
import unittest
from pathlib import Path

from sbk_dashboard.config import RuntimePlatform, load_download_config, parse_configuration


class ConfigurationTest(unittest.TestCase):
    def test_native_port_source_distinguishes_defaults_cli_and_environment(self):
        defaults = parse_configuration([], {}).monitoring
        command_line = parse_configuration(["-prometheus-port", "19090"], {}).monitoring
        environment = parse_configuration([], {"SBK_DASHBOARD_GRAFANA_PORT": "13000"}).monitoring
        self.assertFalse(defaults.port_was_supplied("prometheus"))
        self.assertFalse(defaults.port_was_supplied("grafana"))
        self.assertTrue(command_line.port_was_supplied("prometheus"))
        self.assertTrue(environment.port_was_supplied("grafana"))

    def test_defaults(self):
        config = parse_configuration([], {})
        self.assertEqual(9721, config.dashboard.port)
        self.assertEqual(7, config.dashboard.retention_days)
        self.assertEqual(9090, config.monitoring.prometheus_port)
        self.assertEqual(3000, config.monitoring.grafana_port)
        self.assertFalse(config.dashboard.continue_existing)
        self.assertEqual(8, config.dashboard.http_workers)
        self.assertEqual(64, config.dashboard.http_queue_capacity)
        self.assertEqual(10, config.dashboard.process_log_size_mb)
        self.assertEqual("0.0.0.0", config.dashboard.bind_address)
        self.assertEqual("127.0.0.1", config.monitoring.prometheus_bind_address)
        self.assertEqual("0.0.0.0", config.monitoring.grafana_bind_address)
        self.assertEqual(45, config.dashboard.prometheus_startup_timeout_seconds)
        self.assertEqual(120, config.dashboard.grafana_startup_timeout_seconds)
        self.assertEqual(60, config.dashboard.status_interval_seconds)
        self.assertEqual("127.0.0.1", config.dashboard.default_target_host)

    def test_command_line_overrides_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            cli_directory = Path(temporary) / "cli"
            environment_directory = Path(temporary) / "environment"
            config = parse_configuration(
                ["-data", str(cli_directory), "-retention", "30", "-prometheus-port", "9191", "-continue", "true",
                 "-bind", "127.0.0.1", "-prometheus-bind", "::1", "-log-level", "debug",
                 "-status-seconds", "15"],
                {"SBK_DASHBOARD_DATA_DIR": str(environment_directory),
                 "SBK_DASHBOARD_DISK_RETENTION_DAYS": "14", "SBK_DASHBOARD_BIND": "0.0.0.0",
                 "SBK_DASHBOARD_STATUS_SECONDS": "30"},
            )
            self.assertEqual(cli_directory.resolve(), config.dashboard.data_directory)
            self.assertEqual(30, config.dashboard.retention_days)
            self.assertEqual(9191, config.monitoring.prometheus_port)
            self.assertTrue(config.dashboard.continue_existing)
            self.assertEqual("127.0.0.1", config.dashboard.bind_address)
            self.assertEqual("::1", config.monitoring.prometheus_bind_address)
            self.assertEqual("DEBUG", config.dashboard.log_level)
            self.assertEqual(15, config.dashboard.status_interval_seconds)
            self.assertEqual("command line", config.dashboard.sources["status-seconds"])

    def test_long_port_option_is_supported_and_reported(self):
        config = parse_configuration(["--port", "19721"], {})
        self.assertEqual(19721, config.dashboard.port)
        self.assertEqual("command line", config.dashboard.sources["port"])
        self.assertEqual(
            Path.home() / ".sbk-dashboard" / "instances" / "19721",
            config.dashboard.data_directory,
        )

    def test_explicit_data_directory_remains_authoritative_on_nondefault_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = parse_configuration(
                ["-port", "19721"],
                {"SBK_DASHBOARD_DATA_DIR": temporary},
            )
            self.assertEqual(Path(temporary).resolve(), config.dashboard.data_directory)
            self.assertEqual(
                "environment SBK_DASHBOARD_DATA_DIR",
                config.dashboard.sources["data"],
            )

    def test_environment_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = parse_configuration([], {
                "SBK_DASHBOARD_DATA_DIR": temporary,
                "SBK_DASHBOARD_DISK_RETENTION_DAYS": "11",
                "SBK_DASHBOARD_SCRAPE_SECONDS": "9",
                "SBK_DASHBOARD_GRAFANA_URL": "https://grafana.example/base",
                "SBK_DASHBOARD_STATUS_SECONDS": "75",
                "SBK_DASHBOARD_DEFAULT_TARGET_HOST": "host.docker.internal",
            })
            self.assertEqual(Path(temporary).resolve(), config.dashboard.data_directory)
            self.assertEqual(11, config.dashboard.retention_days)
            self.assertEqual(9, config.dashboard.scrape_interval_seconds)
            self.assertEqual("https://grafana.example/base", config.monitoring.grafana_public_url)
            self.assertEqual(75, config.dashboard.status_interval_seconds)
            self.assertEqual("host.docker.internal", config.dashboard.default_target_host)
            self.assertEqual(
                "environment SBK_DASHBOARD_DEFAULT_TARGET_HOST",
                config.dashboard.sources["default-target-host"],
            )
            self.assertEqual(
                "environment SBK_DASHBOARD_STATUS_SECONDS", config.dashboard.sources["status-seconds"]
            )

    def test_production_limits_are_configurable_and_bounded(self):
        config = parse_configuration([], {
            "SBK_DASHBOARD_HTTP_WORKERS": "12",
            "SBK_DASHBOARD_HTTP_QUEUE": "96",
            "SBK_DASHBOARD_REQUEST_TIMEOUT_SECONDS": "20",
            "SBK_DASHBOARD_PROCESS_LOG_MB": "25",
            "SBK_DASHBOARD_PROCESS_LOG_BACKUPS": "4",
            "SBK_DASHBOARD_MAX_TARGETS": "500",
            "SBK_DASHBOARD_TARGET_HEALTH_TIMEOUT_SECONDS": "8",
            "SBK_DASHBOARD_PROMETHEUS_STARTUP_TIMEOUT_SECONDS": "60",
            "SBK_DASHBOARD_GRAFANA_STARTUP_TIMEOUT_SECONDS": "180",
        })
        self.assertEqual(12, config.dashboard.http_workers)
        self.assertEqual(96, config.dashboard.http_queue_capacity)
        self.assertEqual(20, config.dashboard.request_timeout_seconds)
        self.assertEqual(25, config.dashboard.process_log_size_mb)
        self.assertEqual(4, config.dashboard.process_log_backups)
        self.assertEqual(500, config.dashboard.max_targets)
        self.assertEqual(8, config.dashboard.target_health_timeout_seconds)
        self.assertEqual(60, config.dashboard.prometheus_startup_timeout_seconds)
        self.assertEqual(180, config.dashboard.grafana_startup_timeout_seconds)
        with self.assertRaisesRegex(ValueError, "SBK_DASHBOARD_HTTP_WORKERS"):
            parse_configuration([], {"SBK_DASHBOARD_HTTP_WORKERS": "129"})

    def test_rejects_authentication(self):
        with self.assertRaisesRegex(ValueError, "future release"):
            parse_configuration(["-auth", "true"], {})

    def test_rejects_invalid_values(self):
        for arguments in (["-port", "0"], ["-retention", "0"], ["-continue", "maybe"],
                          ["-grafana-url", "ftp://host"], ["-bind", "http://bad"],
                          ["-prometheus-bind", "bad host"], ["-log-level", "verbose"],
                          ["-status-seconds", "0"], ["-status-seconds", "86401"]):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                parse_configuration(list(arguments), {})

        with self.assertRaisesRegex(ValueError, "default target host"):
            parse_configuration([], {"SBK_DASHBOARD_DEFAULT_TARGET_HOST": "host:9718"})

    def test_rejects_malformed_ipv4_like_bind_addresses(self):
        for address in ("0.0.0.0.0", "127.000.000.001", "::::", "host:80"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                parse_configuration(["-bind", address], {})

    def test_platform_normalization(self):
        values = {
            ("Linux", "amd64"): "linux-x86_64", ("Darwin", "arm64"): "macos-arm64",
            ("Windows 11", "x64"): "windows-x86_64", ("linux", "aarch64"): "linux-arm64",
        }
        for names, expected in values.items():
            with self.subTest(names=names):
                self.assertEqual(expected, RuntimePlatform.from_names(*names).id)

    def test_download_definitions_exist_for_every_platform(self):
        with tempfile.TemporaryDirectory() as temporary:
            for system in ("linux", "macos", "windows"):
                for architecture in ("x86_64", "arm64"):
                    config = load_download_config(None, Path(temporary), {}, RuntimePlatform(system, architecture))
                    self.assertTrue(config.prometheus.url.startswith("https://"))
                    self.assertTrue(config.grafana.url.startswith("https://"))
                    self.assertEqual(64, len(config.prometheus.sha256))

    def test_legacy_external_properties_override_platform(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "override.properties"
            path.write_text(
                "prometheus.download.url=https://example.test/prom.tar.gz\n"
                "prometheus.download.file=prom.tar.gz\n"
                "prometheus.download.sha256=" + "a" * 64 + "\n"
                "prometheus.archive.directory=prom\n"
                "prometheus.executable=prometheus\n"
                "prometheus.archive.format=tar.gz\n",
                encoding="utf-8",
            )
            config = load_download_config(str(path), Path(temporary), {}, RuntimePlatform("linux", "x86_64"))
            self.assertEqual("https://example.test/prom.tar.gz", config.prometheus.url)

    def test_download_limit_and_properties_source_are_loaded_from_explicit_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "override.properties"
            path.write_text("download.max.bytes=123456\n", encoding="utf-8")
            config = load_download_config(
                None,
                Path(temporary),
                {"SBK_DASHBOARD_MONITORING_PROPERTIES": str(path)},
                RuntimePlatform("linux", "x86_64"),
            )
            self.assertEqual(123456, config.max_download_bytes)
            self.assertEqual(
                "environment SBK_DASHBOARD_MONITORING_PROPERTIES", config.selection_source
            )

    def test_download_limit_must_be_positive_numeric_property(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "override.properties"
            for value in ("0", "not-a-number"):
                with self.subTest(value=value):
                    path.write_text(f"download.max.bytes={value}\n", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "download.max.bytes"):
                        load_download_config(
                            str(path), Path(temporary), {}, RuntimePlatform("linux", "x86_64")
                        )


if __name__ == "__main__":
    unittest.main()

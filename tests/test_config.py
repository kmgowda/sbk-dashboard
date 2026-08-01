import tempfile
import unittest
from pathlib import Path

from sbk_dashboard.config import RuntimePlatform, load_download_config, parse_configuration


class ConfigurationTest(unittest.TestCase):
    def test_defaults(self):
        config = parse_configuration([], {})
        self.assertEqual(9721, config.dashboard.port)
        self.assertEqual(7, config.dashboard.retention_days)
        self.assertEqual(9090, config.monitoring.prometheus_port)
        self.assertEqual(3000, config.monitoring.grafana_port)
        self.assertFalse(config.dashboard.continue_existing)

    def test_command_line_overrides_environment(self):
        config = parse_configuration(
            ["-data", "/cli", "-retention", "30", "-prometheus-port", "9191", "-continue", "true"],
            {"SBK_DASHBOARD_DATA_DIR": "/environment", "SBK_DASHBOARD_DISK_RETENTION_DAYS": "14"},
        )
        self.assertEqual(Path("/cli"), config.dashboard.data_directory)
        self.assertEqual(30, config.dashboard.retention_days)
        self.assertEqual(9191, config.monitoring.prometheus_port)
        self.assertTrue(config.dashboard.continue_existing)

    def test_environment_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = parse_configuration([], {
                "SBK_DASHBOARD_DATA_DIR": temporary,
                "SBK_DASHBOARD_DISK_RETENTION_DAYS": "11",
                "SBK_DASHBOARD_SCRAPE_SECONDS": "9",
                "SBK_DASHBOARD_GRAFANA_URL": "https://grafana.example/base",
            })
            self.assertEqual(Path(temporary), config.dashboard.data_directory)
            self.assertEqual(11, config.dashboard.retention_days)
            self.assertEqual(9, config.dashboard.scrape_interval_seconds)
            self.assertEqual("https://grafana.example/base", config.monitoring.grafana_public_url)

    def test_rejects_authentication(self):
        with self.assertRaisesRegex(ValueError, "future release"):
            parse_configuration(["-auth", "true"], {})

    def test_rejects_invalid_values(self):
        for arguments in (["-port", "0"], ["-retention", "0"], ["-continue", "maybe"],
                          ["-grafana-url", "ftp://host"]):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                parse_configuration(list(arguments), {})

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


if __name__ == "__main__":
    unittest.main()

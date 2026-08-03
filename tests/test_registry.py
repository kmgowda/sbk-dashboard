import json
import tempfile
import unittest
from pathlib import Path

from sbk_dashboard.registry import TargetRegistry


class TargetRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_registers_and_reloads_java_compatible_json(self):
        target = TargetRegistry(self.directory).register("Primary", "HOST.example", 9718, "/metrics")
        reloaded = TargetRegistry(self.directory).find(target.id)
        self.assertEqual(target, reloaded)
        persisted = json.loads((self.directory / "targets.json").read_text())
        self.assertIn("metricsPath", persisted[0])
        self.assertIn("createdAt", persisted[0])
        self.assertEqual("SBK", persisted[0]["kind"])

    def test_identifier_is_stable_and_host_is_case_insensitive(self):
        first = TargetRegistry(self.directory).register("First", "Host.Example", 9718, None)
        self.assertEqual("2c90e1742a4890fb", first.id)
        with self.assertRaisesRegex(ValueError, "already registered"):
            TargetRegistry(self.directory).register("Again", "host.example", 9718, "/other")

    def test_same_host_different_port(self):
        registry = TargetRegistry(self.directory)
        registry.register("One", "host", 9718, "/metrics")
        registry.register("Two", "host", 9719, "/metrics")
        self.assertEqual(2, len(registry.list()))

    def test_default_name_path_and_ipv6_address(self):
        target = TargetRegistry(self.directory).register(None, "[2001:db8::1]", 9718, None)
        self.assertEqual("2001:db8::1:9718", target.name)
        self.assertEqual("/metrics", target.metrics_path)
        self.assertEqual("[2001:db8::1]:9718", target.prometheus_address)

    def test_remove_is_persistent(self):
        registry = TargetRegistry(self.directory)
        target = registry.register("One", "host", 9718, "/metrics")
        self.assertTrue(registry.remove(target.id))
        self.assertFalse(registry.remove(target.id))
        self.assertEqual([], TargetRegistry(self.directory).list())

    def test_restore_is_persistent_and_preserves_target_identity(self):
        registry = TargetRegistry(self.directory)
        target = registry.register("One", "host.example", 9718, "/metrics")
        registry.remove(target.id)
        registry.restore(target)
        self.assertEqual(target, TargetRegistry(self.directory).find(target.id))

    def test_rejects_malformed_input(self):
        registry = TargetRegistry(self.directory)
        values = [
            ("http://host", 9718, "/metrics"), ("host", 0, "/metrics"),
            ("host", 9718, "metrics"), ("host", 9718, "/metrics?q=1"),
            ("0.0.0.0.0", 9718, "/metrics"), ("127.000.000.001", 9718, "/metrics"),
            ("::::", 9718, "/metrics"), ("host:80", 9718, "/metrics"),
        ]
        for host, port, path in values:
            with self.subTest(value=(host, port, path)), self.assertRaises(ValueError):
                registry.register("Bad", host, port, path)

    def test_normalizes_valid_ip_and_dns_hosts(self):
        registry = TargetRegistry(self.directory)
        self.assertEqual("host.example", registry.register("DNS", "HOST.Example.", 9718, None).host)
        self.assertEqual("127.0.0.1", registry.register("IPv4", "127.0.0.1", 9719, None).host)
        self.assertEqual("2001:db8::1", registry.register("IPv6", "[2001:0db8::1]", 9720, None).host)

    def test_enforces_configured_endpoint_limit(self):
        registry = TargetRegistry(self.directory, max_targets=1)
        registry.register("One", "host", 9718, "/metrics")
        with self.assertRaisesRegex(ValueError, "Endpoint limit"):
            registry.register("Two", "host", 9719, "/metrics")

    def test_rejects_boolean_and_out_of_range_persisted_ports(self):
        path = self.directory / "targets.json"
        registry = TargetRegistry(self.directory)
        registry.register("One", "host", 9718, "/metrics")
        original = json.loads(path.read_text(encoding="utf-8"))
        for invalid in (True, False, 0, 65536, 1.5):
            with self.subTest(port=invalid):
                corrupted = [dict(original[0], port=invalid)]
                path.write_text(json.dumps(corrupted), encoding="utf-8")
                with self.assertRaisesRegex(OSError, "Unable to load endpoint registry"):
                    TargetRegistry(self.directory)

    def test_revalidates_all_security_sensitive_persisted_fields(self):
        path = self.directory / "targets.json"
        TargetRegistry(self.directory).register("One", "host.example", 9718, "/metrics")
        original = json.loads(path.read_text(encoding="utf-8"))[0]
        invalid_values = (
            {"host": "host:80"},
            {"metricsPath": "../metrics"},
            {"metricsPath": "/metrics?query=true"},
            {"id": "../../escaped"},
            {"id": "0" * 16},
            {"name": ""},
            {"name": "x" * 101},
            {"createdAt": False},
            {"kind": "unknown"},
        )
        for replacement in invalid_values:
            with self.subTest(replacement=replacement):
                path.write_text(json.dumps([{**original, **replacement}]), encoding="utf-8")
                with self.assertRaisesRegex(OSError, "Unable to load endpoint registry"):
                    TargetRegistry(self.directory)

    def test_persisted_host_is_normalized_before_identity_validation(self):
        path = self.directory / "targets.json"
        target = TargetRegistry(self.directory).register("One", "host.example", 9718, "/metrics")
        persisted = target.persisted()
        persisted["host"] = "HOST.EXAMPLE."
        path.write_text(json.dumps([persisted]), encoding="utf-8")
        self.assertEqual(target, TargetRegistry(self.directory).find(target.id))


if __name__ == "__main__":
    unittest.main()

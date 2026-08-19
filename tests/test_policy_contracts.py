# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import json
import unittest
from pathlib import Path

from sbk_dashboard.contracts import DASHBOARD_READINESS_RETRY_DELAYS_SECONDS, DEFAULT_DASHBOARD_PORT
from sbk_dashboard.endpoint_policy import ENDPOINT_ID_HEX_LENGTH, valid_port
from sbk_dashboard.layout import DashboardDataLayout, PortableHomeLayout
from sbk_dashboard.platforms import RuntimePlatform, portable_platform_id
from scripts.resolve_native_artifact import manifest_value
from scripts.sync_release_metadata import synchronize

ROOT = Path(__file__).resolve().parents[1]


class PolicyContractTest(unittest.TestCase):
    def test_platform_normalization_is_shared(self):
        self.assertEqual("macos-arm64", RuntimePlatform.from_names("Darwin", "aarch64").id)
        self.assertEqual("macos-arm64", portable_platform_id("darwin", "aarch64"))
        self.assertEqual("windows-amd64", portable_platform_id("win32", "x86_64"))

    def test_path_layouts_are_deterministic(self):
        home = PortableHomeLayout.from_value("/tmp/sbk-policy-home")
        self.assertEqual(home.root, home.data_directory(DEFAULT_DASHBOARD_PORT))
        self.assertEqual(home.root / "instances/19721", home.data_directory(19721))
        data = DashboardDataLayout(home.root)
        self.assertEqual(home.root / "targets.json", data.targets)
        self.assertEqual(home.root / "monitoring/logs", data.monitoring.logs)

    def test_endpoint_policy_excludes_boolean_ports(self):
        self.assertTrue(valid_port(1))
        self.assertTrue(valid_port(65_535))
        self.assertFalse(valid_port(True))
        self.assertFalse(valid_port(0))
        self.assertEqual(16, ENDPOINT_ID_HEX_LENGTH)

    def test_dashboard_readiness_retry_window_is_bounded(self):
        self.assertEqual(10, len(DASHBOARD_READINESS_RETRY_DELAYS_SECONDS))
        self.assertEqual(37.5, sum(DASHBOARD_READINESS_RETRY_DELAYS_SECONDS))
        self.assertLessEqual(max(DASHBOARD_READINESS_RETRY_DELAYS_SECONDS), 5)

    def test_native_manifest_covers_every_supported_platform(self):
        manifest = json.loads(
            (ROOT / "src/sbk_dashboard/resources/native-artifacts.json").read_text(encoding="utf-8")
        )
        platforms = {
            "linux-x86_64", "linux-arm64", "macos-x86_64", "macos-arm64",
            "windows-x86_64", "windows-arm64",
        }
        for tool in ("prometheus", "grafana"):
            self.assertEqual(platforms, set(manifest["artifacts"][tool]))
            for platform_id in platforms:
                self.assertRegex(manifest_value(manifest, tool, platform_id, "sha256"), r"^[0-9a-f]{64}$")

    def test_grafana_plugin_build_contract_matches_managed_grafana(self):
        manifest = json.loads(
            (ROOT / "src/sbk_dashboard/resources/native-artifacts.json").read_text(encoding="utf-8")
        )
        grafana_versions = {
            value["archiveDirectory"].removeprefix("grafana-")
            for value in manifest["artifacts"]["grafana"].values()
        }
        self.assertEqual(1, len(grafana_versions))
        grafana_version = grafana_versions.pop()
        package = json.loads((ROOT / "grafana-plugin/package.json").read_text(encoding="utf-8"))
        for dependency in ("data", "i18n", "runtime", "schema", "ui"):
            self.assertEqual(grafana_version, package["dependencies"][f"@grafana/{dependency}"])

    def test_release_metadata_matches_version_source(self):
        self.assertEqual([], synchronize(write=False))

if __name__ == "__main__":
    unittest.main()

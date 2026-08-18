# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sbk_dashboard.grafana_plugin import Traversable, install_comparison_plugin
from sbk_dashboard.version import VERSION

ROOT = Path(__file__).resolve().parents[1]


class GrafanaPluginInstallationTest(unittest.TestCase):
    def test_uses_the_resource_traversable_available_for_this_python(self):
        expected_module = "importlib.resources.abc" if sys.version_info >= (3, 11) else "importlib.abc"
        self.assertEqual(expected_module, Traversable.__module__)

    def test_installs_and_replaces_the_bundled_plugin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = install_comparison_plugin(root)
            self.assertTrue((installed / "module.js").is_file())
            self.assertTrue((installed / "plugin.json").is_file())
            descriptor = json.loads((installed / "plugin.json").read_text(encoding="utf-8"))
            self.assertRegex(descriptor["info"]["version"], rf"^{VERSION}-build\.[0-9a-f]{{12}}$")
            (installed / "stale.txt").write_text("old", encoding="utf-8")
            self.assertEqual(installed, install_comparison_plugin(root))
            self.assertFalse((installed / "stale.txt").exists())
            self.assertFalse(list(root.glob(".*.staging")))
            self.assertFalse(list(root.glob(".*.previous")))

    def test_copy_failure_preserves_the_previous_plugin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = install_comparison_plugin(root)
            original = (installed / "module.js").read_bytes()
            with (
                patch("sbk_dashboard.grafana_plugin._copy_tree", side_effect=OSError("copy failed")),
                self.assertRaisesRegex(OSError, "copy failed"),
            ):
                install_comparison_plugin(root)
            self.assertEqual(original, (installed / "module.js").read_bytes())

    def test_packaged_plugin_uses_a_cache_busting_build_version(self):
        source = json.loads((ROOT / "grafana-plugin/src/plugin.json").read_text(encoding="utf-8"))
        packaged = json.loads(
            (
                ROOT
                / "src/sbk_dashboard/resources/grafana/plugins/kmg-sbkcomparison-app/plugin.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(VERSION, source["info"]["version"])
        self.assertRegex(packaged["info"]["version"], rf"^{VERSION}-build\.[0-9a-f]{{12}}$")
        self.assertNotEqual(source["info"]["version"], packaged["info"]["version"])

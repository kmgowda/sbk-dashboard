# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sbk_dashboard.grafana_plugin import install_comparison_plugin


class GrafanaPluginInstallationTest(unittest.TestCase):
    def test_installs_and_replaces_the_bundled_plugin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = install_comparison_plugin(root)
            self.assertTrue((installed / "module.js").is_file())
            self.assertTrue((installed / "plugin.json").is_file())
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

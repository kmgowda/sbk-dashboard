import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import build_portable, sbk_dashboard_portable_entry


class PortableReleaseTest(unittest.TestCase):
    @staticmethod
    def fake_pyinstaller(command, **_kwargs):
        dist = Path(command[command.index("--distpath") + 1]) / "sbk-dashboard"
        dist.mkdir(parents=True)
        executable = dist / "sbk-dashboard"
        executable.write_bytes(b"portable executable")
        (dist / "_internal").mkdir()
        (dist / "_internal/runtime.dat").write_bytes(b"runtime")

    def test_portable_entry_dispatches_application_and_internal_guardian(self):
        with (
            patch("sbk_dashboard.main.main") as application,
            patch.dict("os.environ", {"SBK_DASHBOARD_HOME": "/tmp/sbk-portable-test"}),
        ):
            self.assertEqual(0, sbk_dashboard_portable_entry.main(["--version"]))
        application.assert_called_once_with(["--version"])
        with (
            patch("sbk_dashboard.guardian.main", return_value=7) as guardian,
            patch.dict("os.environ", {"SBK_DASHBOARD_HOME": "/tmp/sbk-portable-test"}),
        ):
            self.assertEqual(
                7,
                sbk_dashboard_portable_entry.main(["--internal-guardian", "--parent-pid", "1"]),
            )
        guardian.assert_called_once_with(["--parent-pid", "1"])

    def test_portable_entry_rejects_broad_home(self):
        with (
            patch.dict("os.environ", {"SBK_DASHBOARD_HOME": str(Path.home())}),
            self.assertRaisesRegex(SystemExit, "dedicated subdirectory"),
        ):
            sbk_dashboard_portable_entry.main(["--version"])

    def test_portable_builder_creates_manifested_checksummed_archive(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(build_portable, "application_version", return_value="1.2.3.4"),
            patch.object(build_portable, "current_platform", return_value="linux-amd64"),
            patch.object(
                build_portable.subprocess,
                "run",
                side_effect=self.fake_pyinstaller,
            ),
        ):
            archive = build_portable.build_bundle(Path(temporary))
            self.assertEqual("sbk-dashboard-1.2.3.4-linux-amd64.tar.gz", archive.name)
            checksum = archive.with_suffix(archive.suffix + ".sha256").read_text(encoding="utf-8")
            self.assertTrue(checksum.endswith(f"  {archive.name}\n"))
            with tarfile.open(archive, "r:gz") as source:
                manifest_member = source.getmember("sbk-dashboard-1.2.3.4-linux-amd64/manifest.json")
                manifest = json.load(source.extractfile(manifest_member))
            self.assertEqual("1.2.3.4", manifest["version"])
            self.assertEqual("linux-amd64", manifest["platform"])
            self.assertIn("sbk-dashboard", manifest["files"])
            self.assertIn("docs/PORTABLE.md", manifest["files"])

    def test_windows_target_always_creates_zip_archive(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(build_portable, "application_version", return_value="1.2.3.4"),
            patch.object(build_portable, "current_platform", return_value="windows-amd64"),
            patch.object(
                build_portable.subprocess,
                "run",
                side_effect=self.fake_pyinstaller,
            ),
        ):
            archive = build_portable.build_bundle(Path(temporary))
            self.assertEqual("sbk-dashboard-1.2.3.4-windows-amd64.zip", archive.name)
            with zipfile.ZipFile(archive) as source:
                self.assertIn(
                    "sbk-dashboard-1.2.3.4-windows-amd64/manifest.json",
                    source.namelist(),
                )

    def test_frozen_processes_dispatch_guardian_through_portable_executable(self):
        source = (build_portable.ROOT / "src/sbk_dashboard/processes.py").read_text(encoding="utf-8")
        self.assertIn('[sys.executable, "--internal-guardian"]', source)

    def test_release_workflow_uses_native_stable_runners_and_attaches_archives(self):
        workflow = (build_portable.ROOT / ".github/workflows/portable.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ubuntu-24.04", workflow)
        self.assertIn("macos-15", workflow)
        self.assertIn("windows-2022", workflow)
        self.assertNotIn("-latest", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("release:\n    types: [published]", workflow)


if __name__ == "__main__":
    unittest.main()

# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
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

    def test_portable_entry_dispatches_launcher_application_and_guardian(self):
        with (
            patch.object(sbk_dashboard_portable_entry, "launcher_main", return_value=3) as launcher,
            patch.dict("os.environ", {"SBK_DASHBOARD_HOME": "/tmp/sbk-portable-test"}),
        ):
            self.assertEqual(3, sbk_dashboard_portable_entry.main(["--version"]))
        launcher.assert_called_once_with(["foreground", "--version"])
        with (
            patch("sbk_dashboard.main.main") as application,
            patch.dict("os.environ", {"SBK_DASHBOARD_HOME": "/tmp/sbk-portable-test"}),
        ):
            self.assertEqual(0, sbk_dashboard_portable_entry.main(["--internal-dashboard", "--version"]))
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

    def test_portable_entry_preserves_lifecycle_modes_and_arguments(self):
        with (
            patch.object(sbk_dashboard_portable_entry, "launcher_main", return_value=0) as launcher,
            patch.dict("os.environ", {"SBK_DASHBOARD_HOME": "/tmp/sbk-portable-test"}),
        ):
            self.assertEqual(
                0,
                sbk_dashboard_portable_entry.main(
                    ["background", "-name", "Dashboard with spaces", "-port", "19721"]
                ),
            )
        launcher.assert_called_once_with(
            ["background", "-name", "Dashboard with spaces", "-port", "19721"]
        )

    def test_portable_entry_rejects_broad_home(self):
        with (
            patch.dict("os.environ", {"SBK_DASHBOARD_HOME": str(Path.home())}),
            self.assertRaisesRegex(SystemExit, "dedicated subdirectory"),
        ):
            sbk_dashboard_portable_entry.main(["--version"])

    def test_direct_portable_repair_has_actionable_message(self):
        with (
            patch.dict("os.environ", {"SBK_DASHBOARD_HOME": "/tmp/sbk-portable-test"}),
            self.assertRaisesRegex(SystemExit, "source-checkout root launcher"),
        ):
            sbk_dashboard_portable_entry.main(["repair"])

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

    @unittest.skipUnless(os.name == "posix" and platform.system() in {"Linux", "Darwin"}, "POSIX portable smoke")
    def test_python_free_installer_installs_then_reuses_cached_runtime(self):
        version = build_portable.application_version()
        system_id = "linux" if platform.system() == "Linux" else "macos"
        machine = platform.machine().lower()
        architecture = "amd64" if machine in {"x86_64", "amd64"} else "arm64"
        platform_id = f"{system_id}-{architecture}"
        archive_name = f"sbk-dashboard-{version}-{platform_id}.tar.gz"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            bundle = root / f"sbk-dashboard-{version}-{platform_id}"
            release.mkdir()
            bundle.mkdir()
            executable = bundle / "sbk-dashboard"
            executable.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$SBK_DASHBOARD_HOME\" "
                "\"$SBK_DASHBOARD_BOOTSTRAP_RUNTIME_KIND\" "
                "\"$SBK_DASHBOARD_BOOTSTRAP_RUNTIME_STATE\" \"$@\" "
                '>"$PORTABLE_TEST_OUTPUT"\n',
                encoding="utf-8",
            )
            executable.chmod(0o755)
            archive = release / archive_name
            with tarfile.open(archive, "w:gz") as output:
                output.add(bundle, arcname=bundle.name)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            (release / f"{archive_name}.sha256").write_text(
                f"{checksum.upper()}  {archive_name}\n", encoding="utf-8"
            )
            home = root / "home"
            observed = root / "arguments.txt"
            environment = os.environ.copy()
            environment.update(
                {
                    "SBK_DASHBOARD_HOME": str(home),
                    "SBK_DASHBOARD_PORTABLE_BASE_URL": release.as_uri(),
                    "PORTABLE_TEST_OUTPUT": str(observed),
                }
            )
            command_directory = root / "commands-without-python"
            command_directory.mkdir()
            for command in (
                "awk",
                "chmod",
                "cp",
                "date",
                "dirname",
                "gzip",
                "mkdir",
                "mv",
                "rm",
                "rmdir",
                "sed",
                "sleep",
                "tar",
                "tr",
                "uname",
                "wc",
            ):
                resolved = shutil.which(command)
                self.assertIsNotNone(resolved, command)
                (command_directory / command).symlink_to(resolved)
            checksum_command = next(
                (command for command in ("sha256sum", "shasum", "openssl") if shutil.which(command)),
                None,
            )
            self.assertIsNotNone(checksum_command)
            assert checksum_command is not None
            (command_directory / checksum_command).symlink_to(shutil.which(checksum_command))
            environment["PATH"] = str(command_directory)
            installer = build_portable.ROOT / "sbk-dashboard"
            first = subprocess.run(
                [str(installer), "-name", "Dashboard with spaces"],
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(0, first.returncode, first.stdout + first.stderr)
            self.assertIn("Preparing standalone", first.stdout)
            self.assertEqual(
                [
                    str(home),
                    "standalone runtime with bundled Python",
                    "fresh environment created",
                    "foreground",
                    "-name",
                    "Dashboard with spaces",
                ],
                observed.read_text(encoding="utf-8").splitlines(),
            )
            self.assertEqual([], list((home / "cache" / "releases").glob("*.listing-*")))
            shutil.rmtree(release)
            second = subprocess.run(
                [str(installer), "stop", "-port", "19721"],
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(0, second.returncode, second.stdout + second.stderr)
            self.assertNotIn("Preparing standalone", second.stdout)
            self.assertEqual(
                [
                    str(home),
                    "standalone runtime with bundled Python",
                    "saved environment reused",
                    "stop",
                    "-port",
                    "19721",
                ],
                observed.read_text(encoding="utf-8").splitlines(),
            )

    @unittest.skipUnless(os.name == "posix" and platform.system() in {"Linux", "Darwin"}, "POSIX portable smoke")
    def test_python_free_installer_rejects_checksum_traversal_and_special_files(self):
        version = build_portable.application_version()
        system_id = "linux" if platform.system() == "Linux" else "macos"
        architecture = "amd64" if platform.machine().lower() in {"x86_64", "amd64"} else "arm64"
        platform_id = f"{system_id}-{architecture}"
        archive_name = f"sbk-dashboard-{version}-{platform_id}.tar.gz"
        installer = build_portable.ROOT / "scripts/install-portable.sh"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            release.mkdir()
            archive = release / archive_name
            archive.write_bytes(b"not an archive")
            (release / f"{archive_name}.sha256").write_text("0" * 64 + "  " + archive_name + "\n")
            environment = os.environ.copy()
            environment.update(
                {
                    "SBK_DASHBOARD_HOME": str(root / "checksum-home"),
                    "SBK_DASHBOARD_PORTABLE_BASE_URL": release.as_uri(),
                }
            )
            failed = subprocess.run(
                [str(installer), "foreground"], capture_output=True, text=True, env=environment
            )
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("Checksum verification failed", failed.stderr)

            with tarfile.open(archive, "w:gz") as output:
                entry = tarfile.TarInfo("../escape")
                entry.size = 4
                output.addfile(entry, io.BytesIO(b"bad!"))
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            (release / f"{archive_name}.sha256").write_text(
                f"{checksum}  {archive_name}\n", encoding="utf-8"
            )
            environment["SBK_DASHBOARD_HOME"] = str(root / "traversal-home")
            failed = subprocess.run(
                [str(installer), "foreground"], capture_output=True, text=True, env=environment
            )
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("Unsafe", failed.stderr)
            self.assertFalse((root / "escape").exists())

            with tarfile.open(archive, "w:gz") as output:
                entry = tarfile.TarInfo(
                    f"sbk-dashboard-{version}-{platform_id}/unsupported-fifo"
                )
                entry.type = tarfile.FIFOTYPE
                output.addfile(entry)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            (release / f"{archive_name}.sha256").write_text(
                f"{checksum}  {archive_name}\n", encoding="utf-8"
            )
            environment["SBK_DASHBOARD_HOME"] = str(root / "special-file-home")
            failed = subprocess.run(
                [str(installer), "foreground"], capture_output=True, text=True, env=environment
            )
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("Only regular files and directories", failed.stderr)

            environment.update({"HOME": str(root / "user-home"), "SBK_DASHBOARD_HOME": "~"})
            failed = subprocess.run(
                [str(installer), "foreground"], capture_output=True, text=True, env=environment
            )
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("dedicated subdirectory", failed.stderr)

            for broad_home in ("~/", "/", "//"):
                environment["SBK_DASHBOARD_HOME"] = broad_home
                failed = subprocess.run(
                    [str(installer), "foreground"],
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertNotEqual(0, failed.returncode, broad_home)
                self.assertIn("dedicated subdirectory", failed.stderr)

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
        self.assertNotIn("--clobber", workflow)
        self.assertIn("release:\n    types: [published]", workflow)
        self.assertIn('"pyinstaller==6.22.0"', workflow)
        self.assertIn('"pyinstaller-hooks-contrib==2026.6"', workflow)
        self.assertIn("setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7", workflow)
        builder = (build_portable.ROOT / "scripts/build_portable.py").read_text(encoding="utf-8")
        self.assertIn("sbk_dashboard_launcher.py", builder)

    def test_source_launchers_contain_python_free_fallbacks(self):
        unix = (build_portable.ROOT / "scripts/sbk-dashboard-launch.sh").read_text(encoding="utf-8")
        powershell = (build_portable.ROOT / "scripts/Invoke-SbkDashboard.ps1").read_text(encoding="utf-8")
        unix_installer = (build_portable.ROOT / "scripts/install-portable.sh").read_text(encoding="utf-8")
        self.assertIn('exec "$SCRIPT_DIR/install-portable.sh" "$MODE" "$@"', unix)
        self.assertIn("Install-SbkDashboardPortable.ps1", powershell)
        self.assertIn("SBK_DASHBOARD_PORTABLE_BASE_URL", unix_installer)
        self.assertIn("portable-bootstrap.properties", unix_installer)
        powershell_installer = (
            build_portable.ROOT / "scripts/Install-SbkDashboardPortable.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("SBK_DASHBOARD_PORTABLE_BASE_URL", powershell_installer)
        self.assertIn("portable-bootstrap.properties", powershell_installer)
        self.assertIn("$HomeValue -eq '~'", powershell_installer)
        self.assertIn("$HomeValue -split '[\\\\/]'", powershell_installer)
        self.assertIn("$UnixType -notin @(0, 0x4000, 0x8000)", powershell_installer)
        self.assertIn("SBK_DASHBOARD_BOOTSTRAP_RUNTIME_STATE", powershell_installer)


if __name__ == "__main__":
    unittest.main()

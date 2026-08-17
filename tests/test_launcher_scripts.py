# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import psutil

from scripts import sbk_dashboard_bootstrap, sbk_dashboard_launcher

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "sbk_dashboard_launcher.py"


class LauncherScriptTest(unittest.TestCase):
    def test_bootstrap_installs_missing_application_dependencies_in_active_environment(self):
        completed = subprocess.CompletedProcess([], 0)
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                sbk_dashboard_bootstrap,
                "missing_modules",
                side_effect=[["psutil", "sbk_dashboard"], []],
            ),
            patch.object(
                sbk_dashboard_bootstrap.subprocess,
                "run",
                side_effect=[completed, completed],
            ) as run,
        ):
            home = Path(temporary)
            self.assertTrue(
                sbk_dashboard_bootstrap.prepare_active_environment(ROOT, home, "1.2.3.4")
            )
        self.assertEqual([sys.executable, "-m", "pip", "install", str(ROOT)], run.call_args_list[1].args[0])
        self.assertEqual(str(home / "cache" / "pip"), run.call_args_list[1].kwargs["env"]["PIP_CACHE_DIR"])

    def test_bootstrap_installs_pip_before_application_when_needed(self):
        missing_pip = subprocess.CompletedProcess([], 1)
        completed = subprocess.CompletedProcess([], 0)
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                sbk_dashboard_bootstrap.subprocess,
                "run",
                side_effect=[missing_pip, completed, completed],
            ) as run,
        ):
            sbk_dashboard_bootstrap.install_application(Path(sys.executable), ROOT, Path(temporary))
        self.assertEqual([sys.executable, "-m", "ensurepip", "--upgrade"], run.call_args_list[1].args[0])
        self.assertEqual([sys.executable, "-m", "pip", "install", str(ROOT)], run.call_args_list[2].args[0])

    def test_private_runtime_is_created_once_then_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)

            def create_venv(command, _failure, _environment=None):
                staging = Path(command[-1]).parent
                python = sbk_dashboard_bootstrap.runtime_python(staging)
                python.parent.mkdir(parents=True)
                python.write_text("prepared Python", encoding="utf-8")
                python.chmod(0o700)

            with (
                patch.object(sbk_dashboard_bootstrap, "platform_id", return_value="linux-amd64"),
                patch.object(sbk_dashboard_bootstrap, "run_checked", side_effect=create_venv) as venv,
                patch.object(sbk_dashboard_bootstrap, "install_application") as install,
            ):
                first = sbk_dashboard_bootstrap.install_private_runtime(
                    ROOT, home, "1.2.3.4", "fingerprint"
                )
                second = sbk_dashboard_bootstrap.install_private_runtime(
                    ROOT, home, "1.2.3.4", "fingerprint"
                )
        self.assertEqual("fresh environment created", first.state)
        self.assertEqual("saved environment reused", second.state)
        self.assertEqual(first.python, second.python)
        self.assertEqual(1, venv.call_count)
        install.assert_called_once()

    def test_active_environment_is_prepared_once_then_reused(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                sbk_dashboard_bootstrap,
                "missing_modules",
                side_effect=[["psutil"], [], [], [], []],
            ),
            patch.object(sbk_dashboard_bootstrap, "install_application") as install,
        ):
            home = Path(temporary)
            self.assertTrue(
                sbk_dashboard_bootstrap.prepare_active_environment(ROOT, home, "1.2.3.4")
            )
            self.assertFalse(
                sbk_dashboard_bootstrap.prepare_active_environment(ROOT, home, "1.2.3.4")
            )
            marker = sbk_dashboard_bootstrap.active_environment_marker(home, ROOT)
            marker.write_text("not JSON", encoding="utf-8")
            self.assertTrue(
                sbk_dashboard_bootstrap.prepare_active_environment(ROOT, home, "1.2.3.4")
            )
        self.assertEqual(2, install.call_count)

    def test_active_environment_kind_distinguishes_venv_and_conda(self):
        with patch.dict(os.environ, {"VIRTUAL_ENV": "/tmp/venv"}, clear=True):
            self.assertEqual(
                "active virtual environment", sbk_dashboard_bootstrap.active_environment_kind()
            )
        with patch.dict(os.environ, {"CONDA_PREFIX": "/tmp/conda"}, clear=True):
            self.assertEqual(
                "active Conda environment", sbk_dashboard_bootstrap.active_environment_kind()
            )

    def test_bootstrap_creates_private_home_runtime_before_launching(self):
        private_python = Path("private") / "venv" / "bin" / "python"
        prepared = sbk_dashboard_bootstrap.PreparedEnvironment(
            private_python,
            "private virtual environment",
            Path("private"),
            "fresh environment created",
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(sbk_dashboard_bootstrap, "environment_is_active", return_value=False),
            patch.object(sbk_dashboard_bootstrap, "dashboard_home", return_value=Path(temporary)),
            patch.object(sbk_dashboard_bootstrap, "project_version", return_value="1.2.3.4"),
            patch.object(
                sbk_dashboard_bootstrap,
                "source_fingerprint",
                return_value="fingerprint",
            ),
            patch.object(
                sbk_dashboard_bootstrap,
                "install_private_runtime",
                return_value=prepared,
            ) as install,
            patch.object(sbk_dashboard_bootstrap, "launch") as launch,
        ):
            self.assertEqual(0, sbk_dashboard_bootstrap.main(["foreground", "--help"]))
        install.assert_called_once_with(ROOT, Path(temporary), "1.2.3.4", "fingerprint", force=False)
        launch.assert_called_once_with(prepared, ROOT / "scripts", ["foreground", "--help"], Path(temporary))

    def test_bootstrap_reuses_active_environment_and_preserves_arguments(self):
        with (
            patch.object(sbk_dashboard_bootstrap, "environment_is_active", return_value=True),
            patch.object(sbk_dashboard_bootstrap, "dashboard_home", return_value=ROOT / "home"),
            patch.object(sbk_dashboard_bootstrap, "project_version", return_value="1.2.3.4"),
            patch.object(sbk_dashboard_bootstrap, "source_fingerprint", return_value="fingerprint"),
            patch.object(
                sbk_dashboard_bootstrap, "prepare_active_environment", return_value=False
            ) as install,
            patch.object(
                sbk_dashboard_bootstrap,
                "active_environment_kind",
                return_value="active virtual environment",
            ),
            patch.object(sbk_dashboard_bootstrap, "launch") as launch,
        ):
            self.assertEqual(0, sbk_dashboard_bootstrap.main(["background", "-port", "19721"]))
        install.assert_called_once_with(ROOT, ROOT / "home", "1.2.3.4", force=False)
        prepared = launch.call_args.args[0]
        self.assertEqual(Path(sys.executable), prepared.python)
        self.assertEqual("saved environment reused", prepared.state)
        self.assertEqual("active virtual environment", prepared.kind)
        self.assertEqual(
            (ROOT / "scripts", ["background", "-port", "19721"], ROOT / "home"),
            launch.call_args.args[1:],
        )

    def test_bootstrap_handoff_reports_runtime_selection(self):
        prepared = sbk_dashboard_bootstrap.PreparedEnvironment(
            Path(sys.executable),
            "private virtual environment",
            ROOT / "saved-runtime",
            "saved environment reused",
        )
        home = ROOT / "portable-home"
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sbk_dashboard_bootstrap.os, "execv") as execute,
        ):
            sbk_dashboard_bootstrap.launch(prepared, ROOT / "scripts", ["foreground", "--help"], home)
            self.assertEqual(
                "private virtual environment",
                os.environ["SBK_DASHBOARD_BOOTSTRAP_RUNTIME_KIND"],
            )
            self.assertEqual(
                "saved environment reused",
                os.environ["SBK_DASHBOARD_BOOTSTRAP_RUNTIME_STATE"],
            )
            self.assertEqual(str(ROOT / "saved-runtime"), os.environ["SBK_DASHBOARD_BOOTSTRAP_RUNTIME_PATH"])
            self.assertEqual(str(home), os.environ["SBK_DASHBOARD_HOME"])
        execute.assert_called_once()

    def test_bootstrap_home_and_fingerprint_are_stable_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "portable-home"
            self.assertEqual(home.resolve(), sbk_dashboard_bootstrap.dashboard_home({"SBK_DASHBOARD_HOME": str(home)}))
            first = sbk_dashboard_bootstrap.source_fingerprint(ROOT)
            second = sbk_dashboard_bootstrap.source_fingerprint(ROOT)
            self.assertRegex(first, r"^[0-9a-f]{16}$")
            self.assertEqual(first, second)
        with self.assertRaisesRegex(SystemExit, "dedicated subdirectory"):
            sbk_dashboard_bootstrap.dashboard_home({"SBK_DASHBOARD_HOME": str(Path.home())})

    def test_source_fingerprint_tracks_resources_and_ignores_generated_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
            (root / "MANIFEST.in").write_text("include resources\n", encoding="utf-8")
            resources = root / "src" / "sbk_dashboard" / "resources"
            resources.mkdir(parents=True)
            banner = resources / "banner.txt"
            banner.write_text("first banner", encoding="utf-8")
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "launcher.ps1").write_text("Write-Host start\n", encoding="utf-8")
            first = sbk_dashboard_bootstrap.source_fingerprint(root)
            banner.write_text("changed banner", encoding="utf-8")
            changed = sbk_dashboard_bootstrap.source_fingerprint(root)
            self.assertNotEqual(first, changed)

            cache = root / "src" / "sbk_dashboard" / "__pycache__"
            cache.mkdir()
            (cache / "generated.pyc").write_bytes(b"generated bytecode")
            metadata = root / "src" / "sbk_dashboard.egg-info"
            metadata.mkdir()
            (metadata / "PKG-INFO").write_text("generated metadata", encoding="utf-8")
            self.assertEqual(changed, sbk_dashboard_bootstrap.source_fingerprint(root))

    def test_windows_process_liveness_uses_handles_without_signaling(self):
        kernel32 = Mock()
        kernel32.OpenProcess.return_value = 123
        kernel32.WaitForSingleObject.return_value = 0x00000102
        self.assertTrue(sbk_dashboard_bootstrap.windows_process_alive(456, kernel32))
        kernel32.OpenProcess.assert_called_once_with(
            sbk_dashboard_bootstrap.WINDOWS_SYNCHRONIZE, False, 456
        )
        kernel32.CloseHandle.assert_called_once_with(123)

        kernel32.reset_mock()
        kernel32.OpenProcess.return_value = 123
        kernel32.WaitForSingleObject.return_value = (
            sbk_dashboard_bootstrap.WINDOWS_WAIT_OBJECT_0
        )
        self.assertFalse(sbk_dashboard_bootstrap.windows_process_alive(456, kernel32))
        kernel32.CloseHandle.assert_called_once_with(123)

    def test_windows_process_liveness_handles_missing_and_inaccessible_pids(self):
        kernel32 = Mock()
        kernel32.OpenProcess.return_value = 0
        with patch.object(
            sbk_dashboard_bootstrap.ctypes,
            "get_last_error",
            return_value=sbk_dashboard_bootstrap.WINDOWS_ERROR_INVALID_PARAMETER,
            create=True,
        ):
            self.assertFalse(sbk_dashboard_bootstrap.windows_process_alive(456, kernel32))
        with patch.object(
            sbk_dashboard_bootstrap.ctypes,
            "get_last_error",
            return_value=5,
            create=True,
        ):
            self.assertTrue(sbk_dashboard_bootstrap.windows_process_alive(456, kernel32))

    def test_process_alive_never_calls_os_kill_on_windows(self):
        with (
            patch.object(sbk_dashboard_bootstrap.os, "name", "nt"),
            patch.object(
                sbk_dashboard_bootstrap, "windows_process_alive", return_value=True
            ) as windows_alive,
            patch.object(sbk_dashboard_bootstrap.os, "kill") as kill,
        ):
            self.assertTrue(sbk_dashboard_bootstrap.process_alive(456))
        windows_alive.assert_called_once_with(456)
        kill.assert_not_called()

    def test_install_lock_recovers_only_dead_stale_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "install.lock"
            path.write_text(json.dumps({"pid": 99999999, "created": 0, "token": "old"}), encoding="utf-8")
            with sbk_dashboard_bootstrap.InstallLock(path):
                self.assertTrue(path.exists())
            self.assertFalse(path.exists())

    def test_failed_repair_preserves_the_previous_private_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with (
                patch.object(sbk_dashboard_bootstrap, "platform_id", return_value="linux-amd64"),
                patch.object(
                    sbk_dashboard_bootstrap,
                    "run_checked",
                    side_effect=SystemExit("simulated venv failure"),
                ),
            ):
                selected = sbk_dashboard_bootstrap.runtime_directory(home, "1.2.3.4", "fingerprint")
                python = sbk_dashboard_bootstrap.runtime_python(selected)
                python.parent.mkdir(parents=True)
                python.write_text("previous runtime", encoding="utf-8")
                python.chmod(0o700)
                sbk_dashboard_bootstrap.atomic_json(
                    sbk_dashboard_bootstrap.installed_marker(selected),
                    {"version": "1.2.3.4", "fingerprint": "fingerprint"},
                )
                with self.assertRaisesRegex(SystemExit, "simulated venv failure"):
                    sbk_dashboard_bootstrap.install_private_runtime(ROOT, home, "1.2.3.4", "fingerprint", force=True)
                self.assertEqual("previous runtime", python.read_text(encoding="utf-8"))

    def test_directory_promotion_refuses_an_existing_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            with self.assertRaisesRegex(FileExistsError, "destination already exists"):
                sbk_dashboard_bootstrap.move_directory(source, destination)
            self.assertTrue(source.is_dir())
            self.assertTrue(destination.is_dir())

    def test_directory_promotion_moves_to_an_absent_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "python-marker").write_text("prepared", encoding="utf-8")
            sbk_dashboard_bootstrap.move_directory(source, destination)
            self.assertFalse(source.exists())
            self.assertEqual(
                "prepared", (destination / "python-marker").read_text(encoding="utf-8")
            )

    def test_private_runtime_recovers_a_crash_between_atomic_renames(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with patch.object(
                sbk_dashboard_bootstrap, "platform_id", return_value="linux-amd64"
            ):
                selected = sbk_dashboard_bootstrap.runtime_directory(
                    home, "1.2.3.4", "fingerprint"
                )
                backup = selected.with_name(f".{selected.name}.backup-interrupted")
                python = sbk_dashboard_bootstrap.runtime_python(backup)
                python.parent.mkdir(parents=True)
                python.write_text("recovered runtime", encoding="utf-8")
                python.chmod(0o700)
                sbk_dashboard_bootstrap.atomic_json(
                    sbk_dashboard_bootstrap.installed_marker(backup),
                    {"version": "1.2.3.4", "fingerprint": "fingerprint"},
                )
                recovered = sbk_dashboard_bootstrap.install_private_runtime(
                    ROOT, home, "1.2.3.4", "fingerprint"
                )
                self.assertEqual(sbk_dashboard_bootstrap.runtime_python(selected), recovered.python)
                self.assertEqual("saved environment reused", recovered.state)
                self.assertEqual("recovered runtime", recovered.python.read_text(encoding="utf-8"))

    def test_environment_report_is_actionable_when_application_is_missing(self):
        missing = ModuleNotFoundError("No module named 'sbk_dashboard'", name="sbk_dashboard")
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "SBK_DASHBOARD_HOME": "/tmp/sbk-home",
                    "SBK_DASHBOARD_BOOTSTRAP_RUNTIME_KIND": "private virtual environment",
                    "SBK_DASHBOARD_BOOTSTRAP_RUNTIME_STATE": "saved environment reused",
                    "SBK_DASHBOARD_BOOTSTRAP_RUNTIME_PATH": "/tmp/sbk-runtime",
                },
                clear=True,
            ),
            patch.object(sbk_dashboard_launcher.importlib, "import_module", side_effect=missing),
            redirect_stdout(output),
            self.assertRaisesRegex(SystemExit, "pip install"),
        ):
            sbk_dashboard_launcher.report_environment()
        self.assertIn("Operating system:", output.getvalue())
        self.assertIn("Python available:", output.getvalue())
        self.assertIn("Runtime preparation: saved environment reused", output.getvalue())
        self.assertIn("Runtime location: /tmp/sbk-runtime", output.getvalue())

    def test_successful_environment_report_marks_diagnostics_as_printed(self):
        package = Mock(__version__="1.2.3.4")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sbk_dashboard_launcher.importlib, "import_module", return_value=package),
            redirect_stdout(io.StringIO()),
        ):
            sbk_dashboard_launcher.report_environment()
            self.assertEqual(
                "1", os.environ["SBK_DASHBOARD_BOOTSTRAP_DIAGNOSTICS_REPORTED"]
            )

    def write_fake_dashboard(self, root):
        package = root / "sbk_dashboard"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        application = (
            "import json\n"
            "import os\n"
            "import signal\n"
            "import sys\n"
            "import time\n"
            "def main(arguments=None):\n"
            "    if os.environ.get('FAKE_EXIT_IMMEDIATELY'):\n"
            "        print('fake dashboard startup failure', flush=True)\n"
            "        return\n"
            "    supplied = sys.argv[1:] if arguments is None else arguments\n"
            "    if '-h' in supplied or '--help' in supplied:\n"
            "        print('usage: sbk-dashboard [options]', flush=True)\n"
            "        return\n"
            "    arguments_output = os.environ.get('FAKE_ARGUMENTS_OUTPUT')\n"
            "    if arguments_output:\n"
            "        with open(arguments_output, 'w', encoding='utf-8') as output:\n"
            "            json.dump(supplied, output)\n"
            "    print('fake dashboard console log', flush=True)\n"
            "    running = True\n"
            "    def stop(*_args):\n"
            "        nonlocal running\n"
            "        running = False\n"
            "    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):\n"
            "        if hasattr(signal, name):\n"
            "            signal.signal(getattr(signal, name), stop)\n"
            "    while running:\n"
            "        time.sleep(0.05)\n"
        )
        (package / "main.py").write_text(application, encoding="utf-8")
        (package / "__main__.py").write_text("from .main import main\nmain()\n", encoding="utf-8")

    def assert_processes_exit(self, processes, timeout=12.0):
        deadline = time.monotonic() + timeout
        living = list(processes)
        while living and time.monotonic() < deadline:
            remaining = []
            for process in living:
                try:
                    if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        remaining.append(process)
                except psutil.Error:
                    pass
            living = remaining
            if living:
                time.sleep(0.05)
        self.assertEqual([], living, f"launcher processes survived: {[process.pid for process in living]}")

    def wait_for_descendants(self, process, minimum):
        deadline = time.monotonic() + 5
        descendants = []
        while time.monotonic() < deadline:
            try:
                descendants = process.children(recursive=True)
            except psutil.Error:
                break
            if len(descendants) >= minimum:
                break
            time.sleep(0.05)
        return descendants

    def test_launcher_log_rotation_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "launcher.log"
            original_max = sbk_dashboard_launcher.LOG_MAX_BYTES
            original_backups = sbk_dashboard_launcher.LOG_BACKUPS
            try:
                sbk_dashboard_launcher.LOG_MAX_BYTES = 5
                sbk_dashboard_launcher.LOG_BACKUPS = 2
                sbk_dashboard_launcher.append_log(log_path, b"12345")
                sbk_dashboard_launcher.append_log(log_path, b"67")
                sbk_dashboard_launcher.append_log(log_path, b"8901")
                self.assertEqual(b"8901", log_path.read_bytes())
                self.assertEqual(b"67", log_path.with_name("launcher.log.1").read_bytes())
                self.assertEqual(b"12345", log_path.with_name("launcher.log.2").read_bytes())
            finally:
                sbk_dashboard_launcher.LOG_MAX_BYTES = original_max
                sbk_dashboard_launcher.LOG_BACKUPS = original_backups

    def test_log_drain_returns_immediately_at_eof(self):
        child = Mock()
        child.stdout.read.return_value = b""
        with patch.object(sbk_dashboard_launcher, "append_log") as append:
            sbk_dashboard_launcher.drain_child_output(child, Path("unused.log"))
        child.stdout.read.assert_called_once_with(sbk_dashboard_launcher.READ_CHUNK_BYTES)
        append.assert_not_called()

    def test_wait_for_process_exit_treats_zombie_as_stopped(self):
        process = Mock()
        process.is_running.return_value = True
        process.status.return_value = psutil.STATUS_ZOMBIE
        self.assertEqual([], sbk_dashboard_launcher.wait_for_process_exit([process], 1))

    def test_run_dashboard_acquires_no_process_before_parent_handshake(self):
        stopping = Mock()
        with (
            patch.object(sbk_dashboard_launcher, "wait_for_startup_handshake", return_value=False),
            patch.object(sbk_dashboard_launcher.subprocess, "Popen") as popen,
            patch.object(sbk_dashboard_launcher.threading, "Event", return_value=stopping),
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"SBK_DASHBOARD_LAUNCHER_DIR": temporary}),
        ):
            self.assertEqual(
                1,
                sbk_dashboard_launcher.run_dashboard(
                    os.getpid(),
                    psutil.Process().create_time(),
                    Path(temporary) / "authorized",
                    Path(temporary) / "started",
                    [],
                ),
            )
        popen.assert_not_called()

    def test_stop_continues_cleanup_when_process_exits_before_signal(self):
        process = Mock(pid=123)
        process.children.return_value = []
        state = {"pid": 123, "create_time": 456.0, "mode": "background"}
        with (
            patch.object(sbk_dashboard_launcher, "load_states", return_value=[(9721, state)]),
            patch.object(sbk_dashboard_launcher, "matching_process", return_value=process),
            patch.object(
                sbk_dashboard_launcher,
                "request_stop",
                side_effect=psutil.NoSuchProcess(123),
            ),
            patch.object(sbk_dashboard_launcher, "wait_then_force", return_value=False) as wait,
            patch.object(sbk_dashboard_launcher, "remove_owned_state") as remove,
        ):
            self.assertEqual(0, sbk_dashboard_launcher.stop())
        wait.assert_called_once_with([process], sbk_dashboard_launcher.DEFAULT_STOP_TIMEOUT_SECONDS)
        remove.assert_called_once_with(123, 456.0, 9721)

    def test_management_port_accepts_application_port_forms(self):
        self.assertEqual(9721, sbk_dashboard_launcher.management_port([]))
        self.assertEqual(19721, sbk_dashboard_launcher.management_port(["-port", "19721"]))
        self.assertEqual(19722, sbk_dashboard_launcher.management_port(["--port=19722"]))
        with self.assertRaisesRegex(SystemExit, "between 1 and 65535"):
            sbk_dashboard_launcher.management_port(["-port", "0"])

    def test_frozen_launcher_reenters_executable_for_children(self):
        with (
            patch.object(sbk_dashboard_launcher.sys, "frozen", True, create=True),
            patch.object(sbk_dashboard_launcher.sys, "executable", "/portable/sbk-dashboard"),
        ):
            self.assertEqual(
                ["/portable/sbk-dashboard", "--internal-launcher", "_watch", "1", "2"],
                sbk_dashboard_launcher.launcher_command("_watch", "1", "2"),
            )
            self.assertEqual(
                ["/portable/sbk-dashboard", "--internal-dashboard", "-port", "19721"],
                sbk_dashboard_launcher.dashboard_command(["-port", "19721"]),
            )

    def test_empty_windows_local_app_data_falls_back_to_home(self):
        fallback = Path("C:/Users/tester")
        expected = fallback / ".sbk-dashboard" / "launcher"
        path_factory = Mock(return_value=fallback)
        path_factory.home.return_value = fallback
        with (
            patch.object(sbk_dashboard_launcher.os, "name", "nt"),
            patch.dict(os.environ, {"LOCALAPPDATA": ""}, clear=False),
            patch.object(sbk_dashboard_launcher, "Path", path_factory),
        ):
            self.assertEqual(expected, sbk_dashboard_launcher.state_directory())

    def test_portable_home_owns_launcher_state_on_every_platform(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"SBK_DASHBOARD_HOME": temporary}, clear=False),
        ):
            self.assertEqual(
                Path(temporary).resolve() / "launcher",
                sbk_dashboard_launcher.state_directory(),
            )

    def test_windows_foreground_stop_writes_identity_specific_request(self):
        process = Mock(pid=321)
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(sbk_dashboard_launcher, "state_directory", return_value=Path(temporary)),
            patch.object(sbk_dashboard_launcher.os, "name", "nt"),
        ):
            sbk_dashboard_launcher.request_stop(process, "foreground", 456.789)
            self.assertTrue(sbk_dashboard_launcher.foreground_stop_path(321, 456.789).exists())
        process.terminate.assert_not_called()

    def test_windows_background_fallback_targets_application_not_watcher(self):
        process = Mock(pid=321)
        watcher = Mock()
        watcher.cmdline.return_value = [sys.executable, str(LAUNCHER), "_watch"]
        dashboard = Mock()
        dashboard.cmdline.return_value = [sys.executable, "-m", "sbk_dashboard"]
        process.children.return_value = [watcher, dashboard]
        with (
            patch.object(sbk_dashboard_launcher.os, "name", "nt"),
            patch.object(signal, "CTRL_BREAK_EVENT", 123, create=True),
            patch.object(sbk_dashboard_launcher.os, "kill", side_effect=OSError("denied")),
        ):
            sbk_dashboard_launcher.request_stop(process, "background", 456.789)
        dashboard.terminate.assert_called_once_with()
        watcher.terminate.assert_not_called()
        process.terminate.assert_not_called()

    def test_wrappers_prefer_active_environments(self):
        start_shell = (ROOT / "scripts" / "start-sbk-dashboard.sh").read_text(encoding="utf-8")
        background_shell = (ROOT / "scripts" / "start-sbk-dashboard-background.sh").read_text(encoding="utf-8")
        stop_shell = (ROOT / "scripts" / "stop-sbk-dashboard.sh").read_text(encoding="utf-8")
        start_powershell = (ROOT / "scripts" / "Start-SbkDashboard.ps1").read_text(encoding="utf-8")
        background_powershell = (ROOT / "scripts" / "Start-SbkDashboardBackground.ps1").read_text(encoding="utf-8")
        stop_powershell = (ROOT / "scripts" / "Stop-SbkDashboard.ps1").read_text(encoding="utf-8")
        unix_common = (ROOT / "scripts" / "sbk-dashboard-launch.sh").read_text(encoding="utf-8")
        powershell_common = (ROOT / "scripts" / "Invoke-SbkDashboard.ps1").read_text(encoding="utf-8")
        self.assertLess(unix_common.index("VIRTUAL_ENV"), unix_common.index("CONDA_PREFIX"))
        self.assertLess(powershell_common.index("VIRTUAL_ENV"), powershell_common.index("CONDA_PREFIX"))
        self.assertIn("sbk_dashboard_bootstrap.py", unix_common)
        self.assertIn("sbk_dashboard_bootstrap.py", powershell_common)
        self.assertIn('sbk-dashboard-launch.sh" foreground "$@"', start_shell)
        self.assertIn('sbk-dashboard-launch.sh" background "$@"', background_shell)
        self.assertIn('sbk-dashboard-launch.sh" stop "$@"', stop_shell)
        self.assertIn("Invoke-SbkDashboard.ps1') foreground @args", start_powershell)
        self.assertIn("Invoke-SbkDashboard.ps1') background @args", background_powershell)
        self.assertIn("Invoke-SbkDashboard.ps1') stop @args", stop_powershell)
        self.assertIn("python_requirement.py", unix_common)
        self.assertIn("python_requirement.py", powershell_common)
        self.assertNotIn("Write-Error @'", start_powershell)
        self.assertNotIn('Write-Error @"', start_powershell)
        self.assertNotIn("recreate the project environment", start_powershell)
        self.assertNotIn("recreate the project environment", background_powershell)
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("foreground_stop_path", launcher)
        self.assertIn("signal.raise_signal(signal.SIGINT)", launcher)

    def test_source_distribution_manifest_includes_launchers(self):
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for name in (
            "Start-SbkDashboard.ps1",
            "Start-SbkDashboardBackground.ps1",
            "Stop-SbkDashboard.ps1",
            "docker_safe_extract.py",
            "resolve_native_artifact.py",
            "python_requirement.py",
            "sbk-dashboard-launch.sh",
            "Invoke-SbkDashboard.ps1",
            "Install-SbkDashboardPortable.ps1",
            "install-portable.sh",
            "portable-bootstrap.properties",
            "build_portable.py",
            "sbk_dashboard_bootstrap.py",
            "sbk_dashboard_launcher.py",
            "sbk_dashboard_portable_entry.py",
            "start-sbk-dashboard.sh",
            "start-sbk-dashboard-background.sh",
            "stop-sbk-dashboard.sh",
        ):
            self.assertIn(f"include scripts/{name}", manifest)
        for name in ("sbk-dashboard", "sbk-dashboard.ps1", "sbk-dashboard.cmd"):
            self.assertIn(f"include {name}", manifest)
        self.assertIn("recursive-include docs *.md", manifest)

    def test_start_and_stop_use_creation_time_guarded_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            arguments_output = root / "arguments.json"
            environment["FAKE_ARGUMENTS_OUTPUT"] = str(arguments_output)
            dashboard_arguments = [
                "-name",
                "Dashboard with spaces",
                "-grafana-url",
                "https://dashboard.example/grafana/",
                "-data",
                str(root / "data with spaces"),
            ]
            start = subprocess.run(
                [sys.executable, str(LAUNCHER), "background", *dashboard_arguments],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            state_path = state_directory / "sbk-dashboard.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            process = psutil.Process(state["pid"])
            descendants = self.wait_for_descendants(process, 2)
            try:
                self.assertGreaterEqual(len(descendants), 2)
                self.assertEqual(dashboard_arguments, json.loads(arguments_output.read_text(encoding="utf-8")))
                self.assertAlmostEqual(state["create_time"], process.create_time(), places=2)
                self.assertEqual("background", state["mode"])
                self.assertIn("Python available:", start.stdout)
                self.assertIn("sbk-dashboard available: version", start.stdout)
                self.assertIn("Started SBK Dashboard", start.stdout)
                duplicate = subprocess.run(
                    [sys.executable, str(LAUNCHER), "background"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertIn("already running", duplicate.stdout)
                stopped = subprocess.run(
                    [sys.executable, str(LAUNCHER), "stop"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertIn("Stopped SBK Dashboard", stopped.stdout)
                self.assertFalse(state_path.exists())
                self.assert_processes_exit([process, *descendants])
                self.assertIn(
                    "fake dashboard console log",
                    (state_directory / "sbk-dashboard.log").read_text(encoding="utf-8"),
                )
            finally:
                if process.is_running():
                    for child in descendants:
                        child.kill()
                    process.kill()

    def test_help_bypasses_an_existing_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            subprocess.run(
                [sys.executable, str(LAUNCHER), "background"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            state = json.loads((state_directory / "sbk-dashboard.json").read_text(encoding="utf-8"))
            process = psutil.Process(state["pid"])
            try:
                helped = subprocess.run(
                    [sys.executable, str(LAUNCHER), "foreground", "--help"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertIn("usage: sbk-dashboard", helped.stdout)
                self.assertTrue(process.is_running())
            finally:
                subprocess.run(
                    [sys.executable, str(LAUNCHER), "stop"],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assert_processes_exit([process])

    def test_multiple_ports_have_independent_state_and_selective_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            processes = []
            try:
                for port in (19721, 19722):
                    subprocess.run(
                        [sys.executable, str(LAUNCHER), "background", "-port", str(port)],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )
                    state = json.loads((state_directory / f"sbk-dashboard-{port}.json").read_text(encoding="utf-8"))
                    self.assertEqual(port, state["port"])
                    processes.append(psutil.Process(state["pid"]))

                selective = subprocess.run(
                    [sys.executable, str(LAUNCHER), "stop", "-port", "19721"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertIn("on port 19721", selective.stdout)
                self.assert_processes_exit([processes[0]])
                self.assertTrue(processes[1].is_running())
                self.assertFalse((state_directory / "sbk-dashboard-19721.json").exists())
                self.assertTrue((state_directory / "sbk-dashboard-19722.json").exists())

                stopped_all = subprocess.run(
                    [sys.executable, str(LAUNCHER), "stop"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertIn("on port 19722", stopped_all.stdout)
                self.assert_processes_exit([processes[1]])
                self.assertFalse((state_directory / "sbk-dashboard-19722.json").exists())
            finally:
                for process in processes:
                    if process.is_running():
                        process.kill()

    def test_stop_all_skips_corrupt_state_and_stops_other_instances(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            subprocess.run(
                [sys.executable, str(LAUNCHER), "background", "-port", "19722"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            state = json.loads((state_directory / "sbk-dashboard-19722.json").read_text(encoding="utf-8"))
            process = psutil.Process(state["pid"])
            (state_directory / "sbk-dashboard.json").write_bytes(b"\xffnot-json")
            try:
                stopped = subprocess.run(
                    [sys.executable, str(LAUNCHER), "stop"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertIn("Skipping unreadable launcher state for port 9721", stopped.stderr)
                self.assertIn("on port 19722", stopped.stdout)
                self.assert_processes_exit([process])
            finally:
                if process.is_running():
                    process.kill()

    def test_concurrent_background_starts_create_only_one_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            starts = [
                subprocess.Popen(
                    [sys.executable, str(LAUNCHER), "background", "-port", "19723"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                )
                for _ in range(2)
            ]
            outputs = [process.communicate(timeout=15) for process in starts]
            self.assertTrue(all(process.returncode == 0 for process in starts))
            combined = "\n".join(output for output, _error in outputs)
            self.assertEqual(1, combined.count("Started SBK Dashboard"))
            self.assertEqual(1, combined.count("already running"))
            state = json.loads((state_directory / "sbk-dashboard-19723.json").read_text(encoding="utf-8"))
            launcher = psutil.Process(state["pid"])
            try:
                subprocess.run(
                    [sys.executable, str(LAUNCHER), "stop", "-port", "19723"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assert_processes_exit([launcher])
            finally:
                if launcher.is_running():
                    launcher.kill()

    def test_force_killed_launcher_does_not_orphan_dashboard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            subprocess.run(
                [sys.executable, str(LAUNCHER), "background"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            state = json.loads((state_directory / "sbk-dashboard.json").read_text(encoding="utf-8"))
            launcher = psutil.Process(state["pid"])
            descendants = self.wait_for_descendants(launcher, 2)
            self.assertGreaterEqual(len(descendants), 2)
            launcher.kill()
            self.assert_processes_exit([launcher, *descendants])
            stopped = subprocess.run(
                [sys.executable, str(LAUNCHER), "stop"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertIn("no matching launcher process", stopped.stdout)

    def test_background_start_does_not_report_success_for_immediate_application_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            environment["FAKE_EXIT_IMMEDIATELY"] = "1"
            started = subprocess.run(
                [sys.executable, str(LAUNCHER), "background"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
                timeout=15,
            )
            self.assertNotEqual(0, started.returncode)
            self.assertNotIn("Started SBK Dashboard", started.stdout)
            self.assertIn("exited during startup", started.stderr)
            self.assertFalse((state_directory / "sbk-dashboard.json").exists())
            self.assertIn(
                "fake dashboard startup failure",
                (state_directory / "sbk-dashboard.log").read_text(encoding="utf-8"),
            )

    def test_interrupting_start_command_cleans_every_acquired_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            started = subprocess.Popen(
                [sys.executable, str(LAUNCHER), "background"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=os.name != "nt",
                creationflags=creation_flags,
            )
            start_process = psutil.Process(started.pid)
            descendants = self.wait_for_descendants(start_process, 2)
            self.assertGreaterEqual(len(descendants), 2)
            if os.name == "nt":
                os.kill(started.pid, signal.CTRL_BREAK_EVENT)
            else:
                started.send_signal(signal.SIGINT)
            started.communicate(timeout=15)
            self.assertNotEqual(0, started.returncode)
            self.assert_processes_exit(descendants)
            self.assertFalse((state_directory / "sbk-dashboard.json").exists())

    def test_stop_refuses_stale_pid_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_directory = Path(temporary)
            (state_directory / "sbk-dashboard.json").write_text(
                json.dumps({"pid": os.getpid(), "create_time": 0}), encoding="utf-8"
            )
            environment = os.environ.copy()
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            stopped = subprocess.run(
                [sys.executable, str(LAUNCHER), "stop"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertIn("no matching launcher process", stopped.stdout)
            self.assertFalse((state_directory / "sbk-dashboard.json").exists())

    def test_corrupt_state_never_allows_a_duplicate_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_directory = Path(temporary)
            state_path = state_directory / "sbk-dashboard.json"
            state_path.write_text("not-json", encoding="utf-8")
            environment = os.environ.copy()
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            started = subprocess.run(
                [sys.executable, str(LAUNCHER), "background"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(0, started.returncode)
            self.assertIn("Unable to read launcher state", started.stderr)
            self.assertTrue(state_path.exists())

    def test_foreground_streams_logs_and_stop_uses_the_same_owned_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            arguments_output = root / "arguments.json"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            environment["FAKE_ARGUMENTS_OUTPUT"] = str(arguments_output)
            dashboard_arguments = ["-name", "foreground dashboard"]
            started = subprocess.Popen(
                [sys.executable, str(LAUNCHER), "foreground", *dashboard_arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            process = psutil.Process(started.pid)
            state_path = state_directory / "sbk-dashboard.json"
            deadline = time.monotonic() + 5
            while not arguments_output.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                self.assertTrue(arguments_output.exists())
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual("foreground", state["mode"])
                self.assertEqual(dashboard_arguments, json.loads(arguments_output.read_text(encoding="utf-8")))
                stopped = subprocess.run(
                    [sys.executable, str(LAUNCHER), "stop"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                output, _ = started.communicate(timeout=12)
                self.assertEqual(0, started.returncode)
                self.assertIn("fake dashboard console log", output)
                self.assertIn("Stopped SBK Dashboard", stopped.stdout)
                self.assertFalse(state_path.exists())
                self.assert_processes_exit([process])
            finally:
                if process.is_running():
                    process.kill()

    def test_ctrl_c_stops_foreground_and_removes_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fake_dashboard(root)
            state_directory = root / "state"
            arguments_output = root / "arguments.json"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["SBK_DASHBOARD_LAUNCHER_DIR"] = str(state_directory)
            environment["FAKE_ARGUMENTS_OUTPUT"] = str(arguments_output)
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            started = subprocess.Popen(
                [sys.executable, str(LAUNCHER), "foreground"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
                start_new_session=os.name != "nt",
                creationflags=creation_flags,
            )
            process = psutil.Process(started.pid)
            deadline = time.monotonic() + 5
            while not arguments_output.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                self.assertTrue(arguments_output.exists())
                if os.name == "nt":
                    os.kill(started.pid, signal.CTRL_BREAK_EVENT)
                else:
                    started.send_signal(signal.SIGINT)
                output, _ = started.communicate(timeout=12)
                self.assertEqual(0, started.returncode)
                self.assertIn("fake dashboard console log", output)
                self.assertFalse((state_directory / "sbk-dashboard.json").exists())
                self.assert_processes_exit([process])
            finally:
                if process.is_running():
                    process.kill()


if __name__ == "__main__":
    unittest.main()

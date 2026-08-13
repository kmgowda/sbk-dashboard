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

from scripts import sbk_dashboard_launcher

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "sbk_dashboard_launcher.py"


class LauncherScriptTest(unittest.TestCase):
    def test_environment_report_is_actionable_when_application_is_missing(self):
        missing = ModuleNotFoundError("No module named 'sbk_dashboard'", name="sbk_dashboard")
        output = io.StringIO()
        with (
            patch.object(sbk_dashboard_launcher.importlib, "import_module", side_effect=missing),
            redirect_stdout(output),
            self.assertRaisesRegex(SystemExit, "pip install"),
        ):
            sbk_dashboard_launcher.report_environment()
        self.assertIn("Python available:", output.getvalue())

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
            patch.object(sbk_dashboard_launcher, "load_state", return_value=state),
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
        remove.assert_called_once_with(123, 456.0)

    def test_windows_foreground_stop_uses_ctrl_break_process_group(self):
        process = Mock(pid=321)
        with (
            patch.object(sbk_dashboard_launcher.os, "name", "nt"),
            patch.object(sbk_dashboard_launcher.signal, "CTRL_BREAK_EVENT", 1, create=True),
            patch.object(sbk_dashboard_launcher.os, "kill") as kill,
        ):
            sbk_dashboard_launcher.request_stop(process, "foreground")
        kill.assert_called_once_with(321, 1)
        process.terminate.assert_not_called()

    def test_wrappers_prefer_active_environments(self):
        start_shell = (ROOT / "scripts" / "start-sbk-dashboard.sh").read_text(encoding="utf-8")
        background_shell = (ROOT / "scripts" / "start-sbk-dashboard-background.sh").read_text(
            encoding="utf-8"
        )
        stop_shell = (ROOT / "scripts" / "stop-sbk-dashboard.sh").read_text(encoding="utf-8")
        start_powershell = (ROOT / "scripts" / "Start-SbkDashboard.ps1").read_text(encoding="utf-8")
        background_powershell = (ROOT / "scripts" / "Start-SbkDashboardBackground.ps1").read_text(
            encoding="utf-8"
        )
        stop_powershell = (ROOT / "scripts" / "Stop-SbkDashboard.ps1").read_text(encoding="utf-8")
        for content in (
            start_shell,
            background_shell,
            stop_shell,
            start_powershell,
            background_powershell,
            stop_powershell,
        ):
            self.assertLess(content.index("VIRTUAL_ENV"), content.index("CONDA_PREFIX"))
            self.assertIn(".venv", content)
            self.assertIn("sbk_dashboard_launcher.py", content)
        self.assertIn('foreground "$@"', start_shell)
        self.assertIn('background "$@"', background_shell)
        self.assertIn("foreground @DashboardArguments", start_powershell)
        self.assertIn("background @DashboardArguments", background_powershell)
        self.assertIn("sys.version_info >= (3, 10)", start_shell)
        self.assertIn("sys.version_info >= (3, 10)", start_powershell)
        self.assertIn("sys.version_info >= (3, 10)", stop_shell)
        self.assertIn("sys.version_info >= (3, 10)", stop_powershell)
        self.assertNotIn("Write-Error @'", start_powershell)
        self.assertNotIn('Write-Error @"', start_powershell)
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('"_foreground"', launcher)
        self.assertIn("terminate_dashboard_group(child.pid, child_process)", launcher)

    def test_source_distribution_manifest_includes_launchers(self):
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for name in (
            "Start-SbkDashboard.ps1",
            "Start-SbkDashboardBackground.ps1",
            "Stop-SbkDashboard.ps1",
            "docker_safe_extract.py",
            "sbk_dashboard_launcher.py",
            "start-sbk-dashboard.sh",
            "start-sbk-dashboard-background.sh",
            "stop-sbk-dashboard.sh",
        ):
            self.assertIn(f"include scripts/{name}", manifest)

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

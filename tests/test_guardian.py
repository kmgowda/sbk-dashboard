import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil


class GuardianIntegrationTest(unittest.TestCase):
    def test_windows_native_process_is_assigned_before_resume(self):
        from sbk_dashboard import guardian

        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "native.json"
            process = MagicMock(pid=123)
            process.wait.return_value = 0
            job = MagicMock()
            with (
                patch.object(guardian.os, "name", "nt"),
                patch.object(guardian, "_parent_alive", return_value=True),
                patch.object(guardian, "WindowsKillOnCloseJob", return_value=job),
                patch.object(guardian.subprocess, "Popen", return_value=process) as popen,
                patch.object(guardian, "atomic_json"),
            ):
                self.assertEqual(0, guardian.guard(1, 2.0, state_path, "Test", ["native.exe"]))
            self.assertEqual(
                guardian.CREATE_SUSPENDED | guardian.CREATE_NEW_PROCESS_GROUP,
                popen.call_args.kwargs["creationflags"],
            )
            job.assign_and_resume.assert_called_once_with(123)
            job.close.assert_called_once_with()

    def test_hard_parent_death_terminates_native_process_and_guardian(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "native.json"
            guardian_path = root / "guardian.txt"
            script = """
import os
import psutil
import subprocess
import sys
import time

state_path, guardian_path = sys.argv[1:]
parent_started = psutil.Process(os.getpid()).create_time()
command = [
    sys.executable, "-m", "sbk_dashboard.guardian",
    "--parent-pid", str(os.getpid()),
    "--parent-started", repr(parent_started),
    "--pid-file", state_path,
    "--name", "Guardian test",
    "--", sys.executable, "-c", "import time; time.sleep(60)",
]
options = (
    {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    if os.name == "nt"
    else {"start_new_session": True}
)
guardian = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **options)
with open(guardian_path, "w", encoding="utf-8") as output:
    output.write(str(guardian.pid))
    output.flush()
time.sleep(60)
"""
            parent = subprocess.Popen(
                [sys.executable, "-c", script, str(state_path), str(guardian_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            guardian_pid = None
            native_pid = None
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and not (state_path.is_file() and guardian_path.is_file()):
                    time.sleep(0.05)
                self.assertTrue(state_path.is_file(), "guardian did not publish the native PID")
                self.assertTrue(guardian_path.is_file(), "parent did not publish the guardian PID")
                native_pid = int(json.loads(state_path.read_text(encoding="utf-8"))["pid"])
                guardian_pid = int(guardian_path.read_text(encoding="utf-8"))
                self.assertEqual(parent.pid, psutil.Process(guardian_pid).ppid())
                self.assertEqual(guardian_pid, psutil.Process(native_pid).ppid())

                parent.kill()
                parent.wait(3)
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline and (
                    psutil.pid_exists(guardian_pid) or psutil.pid_exists(native_pid)
                ):
                    time.sleep(0.05)
                self.assertFalse(psutil.pid_exists(native_pid), "native process survived hard parent death")
                self.assertFalse(psutil.pid_exists(guardian_pid), "guardian did not exit after cleanup")
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(3)
                for pid in (native_pid, guardian_pid):
                    if pid is None:
                        continue
                    try:
                        process = psutil.Process(pid)
                        process.kill()
                        process.wait(3)
                    except psutil.Error:
                        pass


if __name__ == "__main__":
    unittest.main()

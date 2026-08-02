import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil

from sbk_dashboard.processes import (
    LifecycleController,
    LifecycleState,
    ManagedNativeService,
    ManagedProcessRegistry,
    NativeServiceSpec,
    PortProcessManager,
    RotatingProcessLog,
)
from sbk_dashboard.web import BoundedThreadPoolHttpServer


class AlwaysReady:
    def ready(self):
        return True


class LifecycleTest(unittest.TestCase):
    def test_state_machine_accepts_only_defined_transitions(self):
        lifecycle = LifecycleController()
        self.assertEqual(LifecycleState.NEW, lifecycle.state)
        lifecycle.transition(LifecycleState.STARTING)
        lifecycle.transition(LifecycleState.RUNNING)
        lifecycle.transition(LifecycleState.STOPPING)
        lifecycle.transition(LifecycleState.STOPPED)
        with self.assertRaisesRegex(RuntimeError, "Invalid lifecycle transition"):
            lifecycle.transition(LifecycleState.RUNNING)

    def test_managed_service_restarts_crashed_child_and_stops_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = ManagedProcessRegistry(root / "managed.json")
            shutdown = threading.Event()
            command = [sys.executable, "-c", "import time; time.sleep(60)"]
            service = ManagedNativeService(
                NativeServiceSpec(
                    "Test", "test", 12345, lambda: command, AlwaysReady(), root / "test.log", 1024, 2
                ),
                registry,
                shutdown,
            )
            service.start(False)
            first_pid = service.pid
            self.assertIsNotNone(first_pid)
            psutil.Process(first_pid).kill()
            psutil.Process(first_pid).wait(3)
            self.assertTrue(service.supervise())
            second_pid = service.pid
            self.assertNotEqual(first_pid, second_pid)
            service.stop()
            self.assertEqual(LifecycleState.STOPPED, service.lifecycle.state)
            self.assertFalse(psutil.pid_exists(second_pid))

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion")
    def test_stop_terminates_descendants(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
            )
            service = ManagedNativeService(
                NativeServiceSpec(
                    "Tree", "tree", 12346, lambda: [sys.executable, "-c", script], AlwaysReady(),
                    root / "tree.log", 1024, 1,
                ),
                ManagedProcessRegistry(root / "managed.json"),
                threading.Event(),
            )
            service.start(False)
            parent = psutil.Process(service.pid)
            deadline = time.monotonic() + 3
            children = []
            while time.monotonic() < deadline and not children:
                children = parent.children(recursive=True)
                time.sleep(0.05)
            self.assertTrue(children)
            child_pids = [child.pid for child in children]
            service.stop()
            self.assertTrue(all(not psutil.pid_exists(pid) for pid in child_pids))

    def test_log_pump_rotates_and_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "native.log"
            process = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 5000)"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            pump = RotatingProcessLog(process, path, 1024, 2)
            pump.start()
            process.wait(3)
            pump.close()
            files = list(path.parent.glob("native.log*"))
            self.assertLessEqual(len(files), 3)
            self.assertTrue(files)
            self.assertTrue(all(item.stat().st_size <= 1024 for item in files))
            self.assertFalse(any(thread.name == "native-log-pump" for thread in threading.enumerate()))

    def test_port_is_unavailable_when_listener_is_discovered(self):
        listener = SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=19090))
        with (
            patch("sbk_dashboard.processes.psutil.net_connections", return_value=[listener]),
            patch("sbk_dashboard.processes.socket.socket") as socket_type,
        ):
            self.assertFalse(PortProcessManager.available(19090))
        socket_type.assert_not_called()

    def test_windows_port_probe_requests_exclusive_address_use(self):
        with (
            patch("sbk_dashboard.processes.psutil.net_connections", return_value=[]),
            patch("sbk_dashboard.processes.os.name", "nt"),
            patch.object(socket, "SO_EXCLUSIVEADDRUSE", 123, create=True),
            patch("sbk_dashboard.processes.socket.socket") as socket_type,
        ):
            self.assertTrue(PortProcessManager.available(19090))
        probe = socket_type.return_value.__enter__.return_value
        probe.setsockopt.assert_called_once_with(socket.SOL_SOCKET, 123, 1)

    def test_posix_port_probe_reuses_time_wait_addresses(self):
        with (
            patch("sbk_dashboard.processes.psutil.net_connections", return_value=[]),
            patch("sbk_dashboard.processes.os.name", "posix"),
            patch("sbk_dashboard.processes.socket.socket") as socket_type,
        ):
            self.assertTrue(PortProcessManager.available(19090, "127.0.0.1"))
        probe = socket_type.return_value.__enter__.return_value
        probe.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind.assert_called_once_with(("127.0.0.1", 19090))

    def test_port_probe_uses_configured_ipv6_family_and_address(self):
        with (
            patch("sbk_dashboard.processes.psutil.net_connections", side_effect=psutil.Error()),
            patch("sbk_dashboard.processes.socket.socket") as socket_type,
        ):
            self.assertTrue(PortProcessManager.available(19090, "::1"))
        socket_type.assert_called_once_with(socket.AF_INET6, socket.SOCK_STREAM)
        probe = socket_type.return_value.__enter__.return_value
        probe.bind.assert_called_once_with(("::1", 19090))

    def test_inspect_survives_process_disappearing_between_pid_and_exe(self):
        listener = SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=19090), pid=1)
        process = MagicMock()
        process.exe.side_effect = psutil.NoSuchProcess(1)
        process.name.side_effect = psutil.NoSuchProcess(1)
        with (
            patch("sbk_dashboard.processes.psutil.net_connections", return_value=[listener]),
            patch("sbk_dashboard.processes.psutil.Process", return_value=process),
            self.assertRaisesRegex(OSError, "unrelated process"),
        ):
            PortProcessManager._inspect(
                "Prometheus", "prometheus", 19090, "127.0.0.1", {"prometheus"}, MagicMock(), []
            )

    def test_log_pump_does_not_crash_when_log_path_is_unopenable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "log_dir"
            path.mkdir()
            process = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * (16 * 1024 * 1024))"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            pump = RotatingProcessLog(process, path, 1024, 1)
            pump.start()
            process.wait(3)
            pump.close()
            self.assertEqual(0, process.returncode)
            self.assertFalse(pump._thread.is_alive())


class TerminationTest(unittest.TestCase):
    def test_terminate_psutil_tree_continues_when_a_child_disappears(self):
        from sbk_dashboard.processes import _terminate_psutil_tree

        parent = MagicMock()
        child = MagicMock()
        child.terminate.side_effect = psutil.NoSuchProcess(2)
        parent.children.return_value = [child]
        with patch("sbk_dashboard.processes.psutil.wait_procs") as wait_procs:
            wait_procs.side_effect = [([], [parent]), ([], [])]
            _terminate_psutil_tree(parent, "Test")
        parent.terminate.assert_called_once()
        child.terminate.assert_called_once()
        parent.kill.assert_called_once()

    def test_terminate_psutil_tree_cleans_children_after_parent_disappears(self):
        from sbk_dashboard.processes import _terminate_psutil_tree

        parent = MagicMock()
        parent.pid = 10
        parent.terminate.side_effect = psutil.NoSuchProcess(10)
        child = MagicMock()
        parent.children.return_value = [child]
        with patch("sbk_dashboard.processes.psutil.wait_procs") as wait_procs:
            wait_procs.side_effect = [([], [child]), ([], [])]
            _terminate_psutil_tree(parent, "Test")
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual([child], wait_procs.call_args_list[0].args[0])


class BoundedHttpServerTest(unittest.TestCase):
    def test_rejects_requests_beyond_worker_and_queue_capacity(self):
        entered = threading.Event()
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                entered.set()
                release.wait(3)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_):
                return

        server = BoundedThreadPoolHttpServer(("127.0.0.1", 0), Handler, 1, 0, 2)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        first = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
        first.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        self.assertTrue(entered.wait(2))
        second = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
        second.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        self.assertIn(b"503 Service Unavailable", second.recv(1024))
        second.close()
        release.set()
        self.assertIn(b"200 OK", first.recv(1024))
        first.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        server.close_pool()
        self.assertFalse(any(item.name.startswith("sbk-http-worker") for item in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()

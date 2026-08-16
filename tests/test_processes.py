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
from unittest.mock import MagicMock, call, patch

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
    @staticmethod
    def _socket_context():
        context = MagicMock()
        endpoint = MagicMock()
        context.__enter__.return_value = endpoint
        return context, endpoint

    @staticmethod
    def _await_supervised_restart(service, timeout=10):
        """Exercise the supervisor's bounded retry/backoff contract across slower native hosts."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if service.supervise():
                return True
            time.sleep(0.05)
        return False

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
            try:
                first_pid = service.pid
                self.assertIsNotNone(first_pid)
                first_process = psutil.Process(first_pid)
                first_process.kill()
                first_process.wait(3)
                self.assertTrue(self._await_supervised_restart(service))
                second_pid = service.pid
                self.assertIsNotNone(second_pid)
                self.assertNotEqual(first_pid, second_pid)
                service.stop()
                self.assertEqual(LifecycleState.STOPPED, service.lifecycle.state)
                self.assertFalse(psutil.pid_exists(second_pid))
            finally:
                service.stop()

    def test_managed_service_cleans_native_child_if_guardian_is_killed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = ManagedNativeService(
                NativeServiceSpec(
                    "Test", "test", 12347,
                    lambda: [sys.executable, "-c", "import time; time.sleep(60)"],
                    AlwaysReady(), root / "test.log", 1024, 1,
                ),
                ManagedProcessRegistry(root / "managed.json"),
                threading.Event(),
            )
            service.start(False)
            first_native_pid = service.pid
            guardian = psutil.Process(service._process.pid)
            guardian.kill()
            guardian.wait(3)
            if os.name == "nt":
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and psutil.pid_exists(first_native_pid):
                    time.sleep(0.05)
                self.assertFalse(
                    psutil.pid_exists(first_native_pid),
                    "Windows Job Object did not kill the native child when its guardian exited",
                )
            else:
                self.assertTrue(psutil.pid_exists(first_native_pid))
            self.assertTrue(self._await_supervised_restart(service))
            self.assertFalse(psutil.pid_exists(first_native_pid))
            self.assertNotEqual(first_native_pid, service.pid)
            service.stop()

    def test_managed_service_startup_failure_cleans_guardian_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = ManagedProcessRegistry(root / "managed.json")
            service = ManagedNativeService(
                NativeServiceSpec(
                    "Failing", "failing", 12348,
                    lambda: [sys.executable, "-c", "raise SystemExit(7)"],
                    AlwaysReady(), root / "failing.log", 1024, 1,
                ),
                registry,
                threading.Event(),
            )
            with self.assertRaises(OSError):
                service.start(False)
            self.assertIsNone(service.pid)
            self.assertIsNone(registry.find("failing", 12348))
            self.assertEqual([], list(root.glob(".*-guardian-*.json")))

    def test_guardian_handshake_retries_transient_permission_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = ManagedNativeService(
                NativeServiceSpec(
                    "Test", "test", 12349, lambda: [], AlwaysReady(), root / "test.log", 1024, 1
                ),
                ManagedProcessRegistry(root / "managed.json"),
                threading.Event(),
            )
            guardian = MagicMock(pid=4321)
            guardian.poll.return_value = None
            native = MagicMock()
            native.ppid.return_value = guardian.pid
            native.create_time.return_value = 123.5
            state_path = root / "guardian.json"
            with (
                patch.object(
                    Path,
                    "read_text",
                    side_effect=[PermissionError("temporarily locked"), '{"pid": 9876}'],
                ) as read_text,
                patch("sbk_dashboard.processes.psutil.Process", return_value=native),
            ):
                self.assertEqual((9876, 123.5), service._await_guardian_start(guardian, state_path))
            self.assertEqual(2, read_text.call_count)

    def test_guardian_handshake_timeout_reports_last_permission_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = ManagedNativeService(
                NativeServiceSpec(
                    "Test", "test", 12350, lambda: [], AlwaysReady(), root / "test.log", 1024, 1
                ),
                ManagedProcessRegistry(root / "managed.json"),
                threading.Event(),
            )
            guardian = MagicMock(pid=4321)
            guardian.poll.return_value = None
            with (
                patch.object(Path, "read_text", side_effect=PermissionError("persistently locked")),
                patch("sbk_dashboard.processes.time.monotonic", side_effect=[0.0, 0.0, 5.0]),
                self.assertRaisesRegex(OSError, "last state read failed: persistently locked"),
            ):
                service._await_guardian_start(guardian, root / "guardian.json")

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

    def test_port_is_unavailable_when_probe_cannot_listen(self):
        connection_context, connection = self._socket_context()
        probe_context, probe = self._socket_context()
        connection.connect_ex.return_value = 1
        with (
            patch("sbk_dashboard.processes.psutil.net_if_addrs", return_value={}),
            patch("sbk_dashboard.processes.socket.socket", side_effect=[connection_context, probe_context]),
        ):
            probe.listen.side_effect = OSError("address in use")
            self.assertFalse(PortProcessManager.available(19090))
        probe.bind.assert_called_once_with(("0.0.0.0", 19090))
        probe.listen.assert_called_once_with(1)

    def test_port_is_unavailable_when_tcp_listener_accepts_connections(self):
        connection_context, connection = self._socket_context()
        with patch("sbk_dashboard.processes.socket.socket", return_value=connection_context):
            connection.connect_ex.return_value = 0
            self.assertFalse(PortProcessManager.available(19090, "127.0.0.1"))
        connection.settimeout.assert_called_once_with(0.2)
        connection.connect_ex.assert_called_once_with(("127.0.0.1", 19090))
        connection.bind.assert_not_called()

    def test_find_available_port_uses_bounded_next_port_fallback(self):
        with patch.object(
            PortProcessManager,
            "available",
            side_effect=lambda port, _bind: port == 9092,
        ) as available:
            self.assertEqual(9092, PortProcessManager.find_available(9090, "127.0.0.1"))
        self.assertEqual(
            [
                call(9090, "127.0.0.1"),
                call(9091, "127.0.0.1"),
                call(9092, "127.0.0.1"),
            ],
            available.call_args_list,
        )

    def test_find_available_port_skips_an_excluded_port(self):
        with patch.object(PortProcessManager, "available", return_value=True) as available:
            self.assertEqual(
                3001,
                PortProcessManager.find_available(3000, "0.0.0.0", {3000}),
            )
        available.assert_called_once_with(3001, "0.0.0.0")

    def test_find_available_port_search_is_bounded(self):
        with (
            patch.object(PortProcessManager, "available", return_value=False) as available,
            patch("sbk_dashboard.processes.AUTO_PORT_SEARCH_ATTEMPTS", 2),
            self.assertRaisesRegex(OSError, "after 2 attempts"),
        ):
            PortProcessManager.find_available(9090, "127.0.0.1")
        self.assertEqual(3, available.call_count)

    def test_user_supplied_busy_port_reports_owner_and_never_stops_it(self):
        listener = SimpleNamespace(
            status=psutil.CONN_LISTEN,
            laddr=SimpleNamespace(port=19090),
            pid=42,
        )
        owner = MagicMock(pid=42)
        owner.exe.return_value = "/opt/prometheus/prometheus"
        with (
            patch.object(PortProcessManager, "available", return_value=False),
            patch("sbk_dashboard.processes.psutil.net_connections", return_value=[listener]),
            patch("sbk_dashboard.processes.psutil.Process", return_value=owner),
            self.assertRaisesRegex(
                OSError,
                r"Prometheus port 19090.*PID 42 \(/opt/prometheus/prometheus\).*"
                r"environment SBK_DASHBOARD_PROMETHEUS_PORT.*no process was stopped",
            ),
        ):
            PortProcessManager.require_available(
                "Prometheus",
                19090,
                "127.0.0.1",
                "environment SBK_DASHBOARD_PROMETHEUS_PORT",
            )
        owner.terminate.assert_not_called()
        owner.kill.assert_not_called()

    def test_replacement_check_rejects_busy_user_port_even_for_expected_executable(self):
        listener = SimpleNamespace(
            status=psutil.CONN_LISTEN,
            laddr=SimpleNamespace(port=19090),
            pid=42,
        )
        owner = MagicMock(pid=42)
        owner.exe.return_value = "/opt/prometheus/prometheus"
        with (
            patch("sbk_dashboard.processes.psutil.net_connections", return_value=[listener]),
            patch("sbk_dashboard.processes.psutil.Process", return_value=owner),
            self.assertRaisesRegex(OSError, "already in use by PID 42"),
        ):
            PortProcessManager._inspect(
                "Prometheus",
                "prometheus",
                19090,
                "127.0.0.1",
                {"prometheus"},
                MagicMock(),
                [],
                allow_replacement=False,
            )
        owner.terminate.assert_not_called()
        owner.kill.assert_not_called()

    def test_windows_port_probe_requests_exclusive_address_use(self):
        connection_context, connection = self._socket_context()
        probe_context, probe = self._socket_context()
        connection.connect_ex.return_value = 1
        with (
            patch("sbk_dashboard.processes.psutil.net_if_addrs", return_value={}),
            patch("sbk_dashboard.processes.os.name", "nt"),
            patch.object(socket, "SO_EXCLUSIVEADDRUSE", 123, create=True),
            patch("sbk_dashboard.processes.socket.socket", side_effect=[connection_context, probe_context]),
        ):
            self.assertTrue(PortProcessManager.available(19090))
        probe.setsockopt.assert_called_once_with(socket.SOL_SOCKET, 123, 1)
        probe.listen.assert_called_once_with(1)

    def test_windows_port_probe_reuses_only_time_wait_address(self):
        connection_context, connection = self._socket_context()
        exclusive_context, exclusive = self._socket_context()
        reuse_context, reuse = self._socket_context()
        connection.connect_ex.return_value = 1
        exclusive.bind.side_effect = OSError("TIME_WAIT")
        time_wait = SimpleNamespace(
            family=socket.AF_INET,
            laddr=SimpleNamespace(port=19090),
            status=psutil.CONN_TIME_WAIT,
        )
        with (
            patch("sbk_dashboard.processes.psutil.net_if_addrs", return_value={}),
            patch("sbk_dashboard.processes.psutil.net_connections", return_value=[time_wait]),
            patch("sbk_dashboard.processes.os.name", "nt"),
            patch.object(socket, "SO_EXCLUSIVEADDRUSE", 123, create=True),
            patch(
                "sbk_dashboard.processes.socket.socket",
                side_effect=[connection_context, exclusive_context, reuse_context],
            ),
        ):
            self.assertTrue(PortProcessManager.available(19090))
        exclusive.setsockopt.assert_called_once_with(socket.SOL_SOCKET, 123, 1)
        reuse.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reuse.listen.assert_called_once_with(1)

    def test_windows_port_probe_does_not_reuse_non_time_wait_address(self):
        connection_context, connection = self._socket_context()
        exclusive_context, exclusive = self._socket_context()
        connection.connect_ex.return_value = 1
        exclusive.bind.side_effect = OSError("address in use")
        listener = SimpleNamespace(
            family=socket.AF_INET,
            laddr=SimpleNamespace(port=19090),
            status=psutil.CONN_LISTEN,
        )
        with (
            patch("sbk_dashboard.processes.psutil.net_if_addrs", return_value={}),
            patch("sbk_dashboard.processes.psutil.net_connections", return_value=[listener]),
            patch("sbk_dashboard.processes.os.name", "nt"),
            patch.object(socket, "SO_EXCLUSIVEADDRUSE", 123, create=True),
            patch(
                "sbk_dashboard.processes.socket.socket",
                side_effect=[connection_context, exclusive_context],
            ),
        ):
            self.assertFalse(PortProcessManager.available(19090))

    def test_posix_port_probe_reuses_time_wait_addresses(self):
        connection_context, connection = self._socket_context()
        probe_context, probe = self._socket_context()
        connection.connect_ex.return_value = 1
        with (
            patch("sbk_dashboard.processes.os.name", "posix"),
            patch("sbk_dashboard.processes.socket.socket", side_effect=[connection_context, probe_context]),
        ):
            self.assertTrue(PortProcessManager.available(19090, "127.0.0.1"))
        probe.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind.assert_called_once_with(("127.0.0.1", 19090))
        probe.listen.assert_called_once_with(1)

    def test_port_probe_uses_configured_ipv6_family_and_address(self):
        connection_context, connection = self._socket_context()
        probe_context, probe = self._socket_context()
        connection.connect_ex.return_value = 1
        with (
            patch(
                "sbk_dashboard.processes.socket.socket", side_effect=[connection_context, probe_context]
            ) as socket_type,
        ):
            self.assertTrue(PortProcessManager.available(19090, "::1"))
        self.assertEqual(
            [call(socket.AF_INET6, socket.SOCK_STREAM), call(socket.AF_INET6, socket.SOCK_STREAM)],
            socket_type.call_args_list,
        )
        probe.bind.assert_called_once_with(("::1", 19090, 0, 0))
        probe.listen.assert_called_once_with(1)

    def test_port_probe_resolves_ipv6_only_hostname(self):
        connection_context, connection = self._socket_context()
        probe_context, probe = self._socket_context()
        connection.connect_ex.return_value = 1
        resolved = [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2001:db8::8", 19090, 0, 0))]
        with (
            patch("sbk_dashboard.processes.socket.getaddrinfo", return_value=resolved) as getaddrinfo,
            patch(
                "sbk_dashboard.processes.socket.socket", side_effect=[connection_context, probe_context]
            ) as socket_type,
        ):
            self.assertTrue(PortProcessManager.available(19090, "v6-only.example"))
        getaddrinfo.assert_called_once_with(
            "v6-only.example", 19090, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP
        )
        self.assertEqual(
            [call(socket.AF_INET6, socket.SOCK_STREAM), call(socket.AF_INET6, socket.SOCK_STREAM)],
            socket_type.call_args_list,
        )
        connection.connect_ex.assert_called_once_with(("2001:db8::8", 19090, 0, 0))
        probe.bind.assert_called_once_with(("2001:db8::8", 19090, 0, 0))

    def test_wildcard_probe_checks_each_local_address_before_binding(self):
        loopback_context, loopback = self._socket_context()
        public_context, public = self._socket_context()
        loopback.connect_ex.return_value = 1
        public.connect_ex.return_value = 0
        local_addresses = {
            "loopback": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
            "public": [SimpleNamespace(family=socket.AF_INET, address="192.0.2.10")],
        }
        with (
            patch("sbk_dashboard.processes.psutil.net_if_addrs", return_value=local_addresses),
            patch("sbk_dashboard.processes.socket.socket", side_effect=[loopback_context, public_context]),
        ):
            self.assertFalse(PortProcessManager.available(19090))
        loopback.connect_ex.assert_called_once_with(("127.0.0.1", 19090))
        public.connect_ex.assert_called_once_with(("192.0.2.10", 19090))

    def test_ipv6_wildcard_probe_skips_link_local_zone_addresses(self):
        loopback_context, loopback = self._socket_context()
        public_context, public = self._socket_context()
        probe_context, probe = self._socket_context()
        loopback.connect_ex.return_value = 1
        public.connect_ex.return_value = 1
        local_addresses = {
            "test": [
                SimpleNamespace(family=socket.AF_INET6, address="fe80::1%ens192"),
                SimpleNamespace(family=socket.AF_INET6, address="2001:0db8::8"),
            ],
        }
        with (
            patch("sbk_dashboard.processes.psutil.net_if_addrs", return_value=local_addresses),
            patch(
                "sbk_dashboard.processes.socket.socket",
                side_effect=[loopback_context, public_context, probe_context],
            ),
        ):
            self.assertTrue(PortProcessManager.available(19090, "::"))
        loopback.connect_ex.assert_called_once_with(("::1", 19090, 0, 0))
        public.connect_ex.assert_called_once_with(("2001:db8::8", 19090, 0, 0))
        probe.bind.assert_called_once_with(("::", 19090, 0, 0))

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

    def test_log_pump_recovers_after_transient_write_failure(self):
        source = MagicMock()
        source.read.side_effect = [b"lost", b"retained", b""]
        process = SimpleNamespace(stdout=source)
        failed_output = MagicMock()
        failed_output.write.side_effect = OSError("disk temporarily unavailable")
        recovered_output = MagicMock()
        recovered_output.write.return_value = len(b"retained")
        pump = RotatingProcessLog(process, Path("native.log"), 1024, 1)
        with (
            patch.object(pump, "_open_output", side_effect=[(failed_output, 0), (recovered_output, 0)]),
            patch("sbk_dashboard.processes.time.monotonic", side_effect=[0.0, 2.0]),
            self.assertLogs("sbk_dashboard.processes", level="INFO") as captured,
        ):
            pump._run()
        recovered_output.write.assert_called_once_with(b"retained")
        self.assertTrue(any("logging recovered" in message for message in captured.output))

    def test_log_pump_retries_partial_file_writes_without_losing_bytes(self):
        source = MagicMock()
        source.read.side_effect = [b"abcde", b""]
        process = SimpleNamespace(stdout=source)
        output = MagicMock()
        output.write.side_effect = [2, 3]
        pump = RotatingProcessLog(process, Path("native.log"), 1024, 1)
        with patch.object(pump, "_open_output", return_value=(output, 0)):
            pump._run()
        self.assertEqual([call(b"abcde"), call(b"cde")], output.write.call_args_list)

    def test_log_pump_close_reports_worker_that_does_not_stop(self):
        stdout = MagicMock()
        stdout.closed = False
        process = SimpleNamespace(stdout=stdout)
        pump = RotatingProcessLog(process, Path("native.log"), 1024, 1)
        pump._thread = MagicMock()
        pump._thread.is_alive.return_value = True
        with self.assertRaisesRegex(OSError, "did not stop within 3 seconds"):
            pump.close()
        self.assertEqual([call(timeout=2), call(timeout=1)], pump._thread.join.call_args_list)
        stdout.close.assert_called_once()

    def test_managed_service_removes_ownership_when_log_pump_close_fails(self):
        registry = MagicMock()
        service = ManagedNativeService(
            NativeServiceSpec(
                "Test", "test", 19090, lambda: [], AlwaysReady(), Path("native.log"), 1024, 1
            ),
            registry,
            threading.Event(),
        )
        process = MagicMock()
        process.pid = 123
        pump = MagicMock()
        pump.close.side_effect = OSError("pump stuck")
        service._process = process
        service._native_pid = 123
        service._log_pump = pump
        with (
            patch("sbk_dashboard.processes._terminate_owned_process"),
            self.assertRaisesRegex(OSError, "pump stuck"),
        ):
            service._stop_process()
        registry.remove.assert_called_once_with("test", 123)


class TerminationTest(unittest.TestCase):
    def test_finish_descendants_reports_process_that_survives_force(self):
        from sbk_dashboard.processes import _finish_descendants

        child = MagicMock(pid=12)
        with (
            patch("sbk_dashboard.processes.psutil.wait_procs", side_effect=[([], [child]), ([], [child])]),
            self.assertRaisesRegex(OSError, "12"),
        ):
            _finish_descendants([child])
        child.kill.assert_called_once_with()

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

    def test_terminate_psutil_tree_reenumerates_children_before_forced_kill(self):
        from sbk_dashboard.processes import _terminate_psutil_tree

        parent = MagicMock()
        parent.pid = 10
        first = MagicMock()
        first.pid = 11
        late = MagicMock()
        late.pid = 12
        parent.children.side_effect = [[first], [first, late]]
        with patch("sbk_dashboard.processes.psutil.wait_procs") as wait_procs:
            wait_procs.side_effect = [([], [parent, first]), ([], [])]
            _terminate_psutil_tree(parent, "Test")
        first.kill.assert_called_once()
        late.kill.assert_called_once()


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
        try:
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as first:
                first.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                self.assertTrue(entered.wait(2))
                # Admission is rejected immediately after accept, before an HTTP
                # request is read. Sending request bytes races the server close and
                # can produce WSAECONNABORTED instead of exposing the queued 503.
                with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as second:
                    self.assertIn(b"503 Service Unavailable", second.recv(1024))
                release.set()
                self.assertIn(b"200 OK", first.recv(1024))
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)
            server.close_pool()
        self.assertFalse(any(item.name.startswith("sbk-http-worker") for item in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()

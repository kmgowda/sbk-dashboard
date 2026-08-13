#!/usr/bin/env python3
"""Start and stop one foreground or background SBK Dashboard process safely."""

from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

try:
    import psutil
except ModuleNotFoundError as error:
    if error.name != "psutil":
        raise
    raise SystemExit(
        f"Python is available at {sys.executable}, but the required 'psutil' package is missing.\n"
        "From the sbk-dashboard source directory, run:\n"
        f"  {sys.executable} -m pip install .\n"
        "Or install the release wheel into this environment."
    ) from error

STATE_FILE = "sbk-dashboard.json"
LOG_FILE = "sbk-dashboard.log"
DEFAULT_STOP_TIMEOUT_SECONDS = 45.0
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3
READ_CHUNK_BYTES = 64 * 1024
STARTUP_HANDSHAKE_SECONDS = 10.0
BACKGROUND_STARTUP_GRACE_SECONDS = 0.5
WATCH_INTERVAL_SECONDS = 0.25
FORCE_CLEANUP_SECONDS = 10.0


def state_directory() -> Path:
    override = os.environ.get("SBK_DASHBOARD_LAUNCHER_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / "SBK Dashboard" / "launcher"
    return Path.home() / ".sbk-dashboard" / "launcher"


def state_path() -> Path:
    return state_directory() / STATE_FILE


def load_state() -> dict[str, Any] | None:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Unable to read launcher state {state_path()}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"Launcher state {state_path()} is not a JSON object.")
    return value


def matching_process(state: dict[str, Any] | None) -> psutil.Process | None:
    if state is None:
        return None
    try:
        pid = int(state["pid"])
        created = float(state["create_time"])
        process = psutil.Process(pid)
        if abs(process.create_time() - created) > 0.01 or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process
    except (KeyError, TypeError, ValueError, psutil.Error):
        return None


def process_matches(pid: int, create_time: float) -> psutil.Process | None:
    return matching_process({"pid": pid, "create_time": create_time})


def remove_stale_state() -> None:
    with suppress(FileNotFoundError):
        state_path().unlink()


def remove_owned_state(pid: int, create_time: float) -> None:
    try:
        state = load_state()
    except SystemExit:
        return
    if state is None:
        return
    try:
        matches = int(state["pid"]) == pid and abs(float(state["create_time"]) - create_time) <= 0.01
    except (KeyError, TypeError, ValueError):
        return
    if matches:
        remove_stale_state()


def write_state(process: psutil.Process, mode: str) -> None:
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    target = state_path()
    temporary = target.with_suffix(f".tmp-{os.getpid()}")
    payload = {
        "pid": process.pid,
        "create_time": process.create_time(),
        "python": sys.executable,
        "mode": mode,
        "log": str(directory / LOG_FILE),
    }
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def report_environment() -> None:
    print(f"Python available: {sys.version.split()[0]} at {sys.executable}")
    if os.environ.get("VIRTUAL_ENV"):
        print(f"Environment: active virtual environment {os.environ['VIRTUAL_ENV']}")
    elif os.environ.get("CONDA_PREFIX"):
        print(f"Environment: active Conda environment {os.environ['CONDA_PREFIX']}")
    elif sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        print(f"Environment: virtual environment {sys.prefix}")
    else:
        print("Environment: system/PATH Python")
    try:
        package = importlib.import_module("sbk_dashboard")
    except ModuleNotFoundError as error:
        if error.name != "sbk_dashboard":
            raise
        raise SystemExit(
            "sbk-dashboard is not installed in the selected Python environment.\n"
            "From the sbk-dashboard source directory, run:\n"
            f"  {sys.executable} -m pip install .\n"
            "Or install the downloaded release wheel, for example:\n"
            f"  {sys.executable} -m pip install sbk_dashboard-<version>-py3-none-any.whl"
        ) from error
    version = getattr(package, "__version__", "unknown")
    print(f"sbk-dashboard available: version {version}")


def foreground(arguments: list[str]) -> int:
    current = matching_process(load_state())
    if current is not None:
        print(f"SBK Dashboard is already running with PID {current.pid}.")
        return 0
    remove_stale_state()
    report_environment()
    process = psutil.Process(os.getpid())
    process_created = process.create_time()
    write_state(process, "foreground")
    print(f"Starting SBK Dashboard in the foreground with PID {process.pid}.")
    print("Press Ctrl+C to stop SBK Dashboard.")
    try:
        application = importlib.import_module("sbk_dashboard.main")
        application.main(arguments)
    finally:
        remove_owned_state(process.pid, process_created)
    return 0


def start_background(arguments: list[str]) -> int:
    current = matching_process(load_state())
    if current is not None:
        print(f"SBK Dashboard is already running with PID {current.pid}.")
        return 0
    remove_stale_state()
    report_environment()
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    parent = psutil.Process(os.getpid())
    startup_id = uuid.uuid4().hex
    marker = directory / f"startup-{startup_id}.authorized"
    started_marker = directory / f"startup-{startup_id}.started"
    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "_run",
            str(parent.pid),
            str(parent.create_time()),
            str(marker),
            str(started_marker),
            *arguments,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=os.name != "nt",
        creationflags=creation_flags,
        close_fds=True,
    )
    previous_handlers: dict[signal.Signals, Any] = {}

    def interrupt_start(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    handled_signals = [signal.SIGINT, signal.SIGTERM]
    break_signal = vars(signal).get("SIGBREAK")
    if isinstance(break_signal, signal.Signals):
        handled_signals.append(break_signal)
    try:
        for signum in handled_signals:
            previous_handlers[signum] = signal.signal(signum, interrupt_start)
        time.sleep(0.5)
        if child.poll() is not None:
            raise SystemExit(
                f"SBK Dashboard exited during startup with status {child.returncode}. "
                f"See {directory / LOG_FILE}."
            )
        try:
            process = psutil.Process(child.pid)
        except psutil.NoSuchProcess:
            raise SystemExit(
                f"SBK Dashboard exited during startup. See {directory / LOG_FILE}."
            ) from None
        write_state(process, "background")
        marker.touch(exist_ok=False)
        wait_for_background_start(child, started_marker, directory / LOG_FILE)
    except BaseException:
        running_child = None
        if child.poll() is None:
            with suppress(psutil.NoSuchProcess):
                running_child = psutil.Process(child.pid)
        terminate_dashboard_group(child.pid, running_child)
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=FORCE_CLEANUP_SECONDS)
        remove_stale_state()
        for startup_marker in (marker, started_marker):
            with suppress(FileNotFoundError):
                startup_marker.unlink()
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    print(f"Started SBK Dashboard with PID {child.pid} using {sys.executable}.")
    print(f"Log: {directory / LOG_FILE}")
    return 0


def wait_for_background_start(
    supervisor: subprocess.Popen[bytes], started_marker: Path, log_path: Path
) -> None:
    deadline = time.monotonic() + STARTUP_HANDSHAKE_SECONDS
    while time.monotonic() < deadline:
        status = supervisor.poll()
        if status is not None:
            raise SystemExit(
                f"SBK Dashboard exited during startup with status {status}. See {log_path}."
            )
        if started_marker.exists():
            with suppress(FileNotFoundError):
                started_marker.unlink()
            time.sleep(0.1)
            status = supervisor.poll()
            if status is not None:
                raise SystemExit(
                    f"SBK Dashboard exited during startup with status {status}. See {log_path}."
                )
            return
        time.sleep(0.05)
    raise SystemExit(f"SBK Dashboard startup confirmation timed out. See {log_path}.")


def rotate_log(log_path: Path, incoming_bytes: int) -> None:
    try:
        current_size = log_path.stat().st_size
    except FileNotFoundError:
        return
    if current_size + incoming_bytes <= LOG_MAX_BYTES:
        return
    oldest = log_path.with_name(f"{log_path.name}.{LOG_BACKUPS}")
    with suppress(FileNotFoundError):
        oldest.unlink()
    for index in range(LOG_BACKUPS - 1, 0, -1):
        source = log_path.with_name(f"{log_path.name}.{index}")
        destination = log_path.with_name(f"{log_path.name}.{index + 1}")
        with suppress(FileNotFoundError):
            os.replace(source, destination)
    os.replace(log_path, log_path.with_name(f"{log_path.name}.1"))


def append_log(log_path: Path, chunk: bytes) -> None:
    rotate_log(log_path, len(chunk))
    with log_path.open("ab", buffering=0) as output:
        output.write(chunk)


def terminate_dashboard_group(group_id: int, dashboard: psutil.Process | None) -> None:
    if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
        try:
            os.kill(group_id, signal.CTRL_BREAK_EVENT)
            return
        except OSError:
            pass
    elif os.name != "nt":
        try:
            os.killpg(group_id, signal.SIGTERM)
            return
        except ProcessLookupError:
            pass
    if dashboard is not None:
        with suppress(psutil.Error):
            dashboard.terminate()


def force_cleanup(processes: list[psutil.Process]) -> list[psutil.Process]:
    unique = {process.pid: process for process in processes}
    living = []
    for process in reversed(list(unique.values())):
        try:
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                process.kill()
                living.append(process)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error:
            living.append(process)
    return wait_for_process_exit(living, FORCE_CLEANUP_SECONDS)


def wait_for_process_exit(processes: list[psutil.Process], timeout: float) -> list[psutil.Process]:
    deadline = time.monotonic() + timeout
    living = list(processes)
    while living and time.monotonic() < deadline:
        remaining = []
        for process in living:
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    remaining.append(process)
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:
                remaining.append(process)
        living = remaining
        if living:
            time.sleep(WATCH_INTERVAL_SECONDS)
    return living


def wait_then_force(processes: list[psutil.Process], timeout: float) -> bool:
    living = wait_for_process_exit(processes, timeout)
    if not living:
        return False
    remaining = force_cleanup(living)
    if remaining:
        identifiers = ", ".join(str(process.pid) for process in remaining)
        raise SystemExit(f"Unable to stop launcher-owned process(es): {identifiers}.")
    return True


def watch_launcher(parent_pid: int, parent_created: float, child_pid: int, child_created: float) -> int:
    while True:
        child = process_matches(child_pid, child_created)
        if child is None:
            return 0
        if process_matches(parent_pid, parent_created) is None:
            descendants = child.children(recursive=True)
            terminate_dashboard_group(parent_pid, child)
            wait_then_force([child, *descendants], DEFAULT_STOP_TIMEOUT_SECONDS)
            return 0
        time.sleep(WATCH_INTERVAL_SECONDS)


def wait_for_startup_handshake(
    parent_pid: int, parent_created: float, marker: Path, stopping: threading.Event
) -> bool:
    deadline = time.monotonic() + STARTUP_HANDSHAKE_SECONDS
    while time.monotonic() < deadline:
        if marker.exists():
            with suppress(FileNotFoundError):
                marker.unlink()
            return True
        if stopping.is_set() or process_matches(parent_pid, parent_created) is None:
            return False
        time.sleep(0.05)
    return False


def run_dashboard(
    parent_pid: int,
    parent_created: float,
    marker: Path,
    started_marker: Path,
    arguments: list[str],
) -> int:
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / LOG_FILE
    stopping = threading.Event()
    child: subprocess.Popen[bytes] | None = None

    def stop_child(_signum: int, _frame: object) -> None:
        stopping.set()
        if os.name != "nt" and child is not None and child.poll() is None:
            child.terminate()

    handled_signals = [signal.SIGINT, signal.SIGTERM]
    break_signal = vars(signal).get("SIGBREAK")
    if isinstance(break_signal, signal.Signals):
        handled_signals.append(break_signal)
    for signum in handled_signals:
        signal.signal(signum, stop_child)
    if not wait_for_startup_handshake(parent_pid, parent_created, marker, stopping):
        return 1

    supervisor = psutil.Process(os.getpid())
    supervisor_created = supervisor.create_time()
    exit_code = 1
    try:
        child = subprocess.Popen(
            [sys.executable, "-m", "sbk_dashboard", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        child_process = psutil.Process(child.pid)
        startup_deadline = time.monotonic() + BACKGROUND_STARTUP_GRACE_SECONDS
        while time.monotonic() < startup_deadline and child.poll() is None and not stopping.is_set():
            time.sleep(0.05)
        if child.poll() is None and not stopping.is_set():
            watch_creation_flags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            )
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "_watch",
                    str(supervisor.pid),
                    str(supervisor_created),
                    str(child_process.pid),
                    str(child_process.create_time()),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
                creationflags=watch_creation_flags,
                close_fds=True,
            )
            started_marker.touch(exist_ok=False)
        assert child.stdout is not None
        while True:
            chunk = child.stdout.read(READ_CHUNK_BYTES)
            if chunk:
                append_log(log_path, chunk)
            if not chunk and child.poll() is not None:
                break
    finally:
        if child is not None:
            if child.stdout is not None:
                child.stdout.close()
            if child.poll() is None:
                child.terminate()
            with suppress(subprocess.TimeoutExpired):
                exit_code = child.wait(timeout=DEFAULT_STOP_TIMEOUT_SECONDS)
        for startup_marker in (marker, started_marker):
            with suppress(FileNotFoundError):
                startup_marker.unlink()
        remove_owned_state(supervisor.pid, supervisor_created)
    return exit_code


def stop_timeout() -> float:
    raw = os.environ.get("SBK_DASHBOARD_STOP_TIMEOUT", str(DEFAULT_STOP_TIMEOUT_SECONDS))
    try:
        timeout = float(raw)
    except ValueError as error:
        raise SystemExit("SBK_DASHBOARD_STOP_TIMEOUT must be a number.") from error
    if not 1 <= timeout <= 300:
        raise SystemExit("SBK_DASHBOARD_STOP_TIMEOUT must be between 1 and 300 seconds.")
    return timeout


def request_stop(process: psutil.Process, mode: str) -> None:
    if os.name == "nt" and mode == "background" and hasattr(signal, "CTRL_BREAK_EVENT"):
        try:
            os.kill(process.pid, signal.CTRL_BREAK_EVENT)
            return
        except OSError:
            for child in process.children(recursive=True):
                try:
                    if "sbk_dashboard" in " ".join(child.cmdline()):
                        child.terminate()
                        return
                except psutil.Error:
                    continue
            process.terminate()
            return
    process.terminate()


def stop() -> int:
    state = load_state()
    process = matching_process(state)
    if process is None:
        remove_stale_state()
        print("SBK Dashboard is not running (no matching launcher process).")
        return 0
    assert state is not None
    try:
        descendants = process.children(recursive=True)
    except psutil.Error:
        descendants = []
    mode = str(state.get("mode", "background"))
    with suppress(psutil.Error):
        request_stop(process, mode)
    forced = wait_then_force([process, *descendants], stop_timeout())
    remove_owned_state(process.pid, float(state["create_time"]))
    suffix = " after bounded forceful cleanup" if forced else ""
    print(f"Stopped SBK Dashboard PID {process.pid}{suffix}.")
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "_run":
        if len(sys.argv) < 6:
            raise SystemExit("Invalid internal launcher arguments.")
        return run_dashboard(
            int(sys.argv[2]),
            float(sys.argv[3]),
            Path(sys.argv[4]),
            Path(sys.argv[5]),
            sys.argv[6:],
        )
    if len(sys.argv) >= 2 and sys.argv[1] == "_watch":
        if len(sys.argv) != 6:
            raise SystemExit("Invalid internal watcher arguments.")
        return watch_launcher(int(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5]))
    if len(sys.argv) < 2 or sys.argv[1] not in {"foreground", "background", "start", "stop"}:
        raise SystemExit(
            f"Usage: {Path(sys.argv[0]).name} "
            "foreground|background|start [dashboard options...] | stop"
        )
    if sys.argv[1] == "foreground":
        return foreground(sys.argv[2:])
    if sys.argv[1] in {"background", "start"}:
        return start_background(sys.argv[2:])
    return stop()


if __name__ == "__main__":
    raise SystemExit(main())

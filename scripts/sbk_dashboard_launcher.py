#!/usr/bin/env python3
"""Start and stop foreground or background SBK Dashboard instances safely."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import platform
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

try:
    from sbk_dashboard.contracts import (
        BOOTSTRAP_DIAGNOSTICS_REPORTED_ENVIRONMENT,
        BOOTSTRAP_RUNTIME_KIND_ENVIRONMENT,
        BOOTSTRAP_RUNTIME_PATH_ENVIRONMENT,
        BOOTSTRAP_RUNTIME_STATE_ENVIRONMENT,
        DEFAULT_DASHBOARD_PORT,
        DEFAULT_HOME_DIRECTORY_NAME,
        LAUNCHER_DIRECTORY_ENVIRONMENT,
        PORTABLE_HOME_ENVIRONMENT,
        PROCESS_CREATE_TIME_TOLERANCE_SECONDS,
    )
    from sbk_dashboard.endpoint_policy import MAX_TCP_PORT, MIN_TCP_PORT, valid_port
except ModuleNotFoundError as error:
    if error.name not in {"sbk_dashboard", "sbk_dashboard.contracts"}:
        raise
    source = Path(__file__).resolve().parents[1] / "src" / "sbk_dashboard"

    def load_policy_module(name: str) -> Any:
        spec = importlib.util.spec_from_file_location(f"_sbk_launcher_{name}", source / f"{name}.py")
        if spec is None or spec.loader is None:
            raise SystemExit(f"Unable to load launcher policy module: {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    contracts = load_policy_module("contracts")
    endpoint_policy = load_policy_module("endpoint_policy")
    DEFAULT_DASHBOARD_PORT = contracts.DEFAULT_DASHBOARD_PORT
    DEFAULT_HOME_DIRECTORY_NAME = contracts.DEFAULT_HOME_DIRECTORY_NAME
    BOOTSTRAP_DIAGNOSTICS_REPORTED_ENVIRONMENT = (
        contracts.BOOTSTRAP_DIAGNOSTICS_REPORTED_ENVIRONMENT
    )
    BOOTSTRAP_RUNTIME_KIND_ENVIRONMENT = contracts.BOOTSTRAP_RUNTIME_KIND_ENVIRONMENT
    BOOTSTRAP_RUNTIME_PATH_ENVIRONMENT = contracts.BOOTSTRAP_RUNTIME_PATH_ENVIRONMENT
    BOOTSTRAP_RUNTIME_STATE_ENVIRONMENT = contracts.BOOTSTRAP_RUNTIME_STATE_ENVIRONMENT
    LAUNCHER_DIRECTORY_ENVIRONMENT = contracts.LAUNCHER_DIRECTORY_ENVIRONMENT
    PORTABLE_HOME_ENVIRONMENT = contracts.PORTABLE_HOME_ENVIRONMENT
    PROCESS_CREATE_TIME_TOLERANCE_SECONDS = contracts.PROCESS_CREATE_TIME_TOLERANCE_SECONDS
    MAX_TCP_PORT = endpoint_policy.MAX_TCP_PORT
    MIN_TCP_PORT = endpoint_policy.MIN_TCP_PORT
    valid_port = endpoint_policy.valid_port

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


def launcher_command(mode: str, *arguments: str) -> list[str]:
    """Build a source or frozen command that re-enters this launcher."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--internal-launcher", mode, *arguments]
    return [sys.executable, str(Path(__file__).resolve()), mode, *arguments]


def dashboard_command(arguments: list[str]) -> list[str]:
    """Build a source or frozen command that starts the application child."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--internal-dashboard", *arguments]
    return [sys.executable, "-m", "sbk_dashboard", *arguments]


def state_directory() -> Path:
    override = os.environ.get(LAUNCHER_DIRECTORY_ENVIRONMENT)
    if override:
        return Path(override).expanduser().resolve()
    portable_home = os.environ.get(PORTABLE_HOME_ENVIRONMENT, "").strip()
    if portable_home:
        return Path(portable_home).expanduser().resolve() / "launcher"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        legacy = base / "SBK Dashboard" / "launcher"
        if legacy.exists():
            return legacy
        return base / DEFAULT_HOME_DIRECTORY_NAME / "launcher"
    return Path.home().resolve() / DEFAULT_HOME_DIRECTORY_NAME / "launcher"


def state_path(port: int = DEFAULT_DASHBOARD_PORT) -> Path:
    name = STATE_FILE if port == DEFAULT_DASHBOARD_PORT else f"sbk-dashboard-{port}.json"
    return state_directory() / name


def log_path(port: int = DEFAULT_DASHBOARD_PORT) -> Path:
    name = LOG_FILE if port == DEFAULT_DASHBOARD_PORT else f"sbk-dashboard-{port}.log"
    return state_directory() / name


def foreground_stop_path(pid: int, create_time: float) -> Path:
    return state_directory() / f"stop-{pid}-{int(create_time * 1000)}.request"


def load_state(port: int = DEFAULT_DASHBOARD_PORT) -> dict[str, Any] | None:
    target = state_path(port)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"Unable to read launcher state {target}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"Launcher state {target} is not a JSON object.")
    return value


def load_states(port: int | None = None) -> list[tuple[int, dict[str, Any]]]:
    if port is not None:
        state = load_state(port)
        return [] if state is None else [(port, state)]
    directory = state_directory()
    if not directory.exists():
        return []
    records = []
    for path in sorted(directory.glob("sbk-dashboard*.json")):
        name = path.name
        if name == STATE_FILE:
            selected_port = DEFAULT_DASHBOARD_PORT
        else:
            match = name.removeprefix("sbk-dashboard-").removesuffix(".json")
            try:
                selected_port = int(match)
            except ValueError:
                continue
        try:
            state = load_state(selected_port)
        except SystemExit as error:
            print(f"Skipping unreadable launcher state for port {selected_port}: {error}", file=sys.stderr)
            continue
        if state is not None:
            records.append((selected_port, state))
    return records


def matching_process(state: dict[str, Any] | None) -> psutil.Process | None:
    if state is None:
        return None
    try:
        pid = int(state["pid"])
        created = float(state["create_time"])
        process = psutil.Process(pid)
        if (
            abs(process.create_time() - created) > PROCESS_CREATE_TIME_TOLERANCE_SECONDS
            or process.status() == psutil.STATUS_ZOMBIE
        ):
            return None
        return process
    except (KeyError, TypeError, ValueError, psutil.Error):
        return None


def process_matches(pid: int, create_time: float) -> psutil.Process | None:
    return matching_process({"pid": pid, "create_time": create_time})


def remove_stale_state(port: int = DEFAULT_DASHBOARD_PORT) -> None:
    with suppress(FileNotFoundError):
        state_path(port).unlink()


def remove_owned_state(pid: int, create_time: float, port: int = DEFAULT_DASHBOARD_PORT) -> None:
    try:
        state = load_state(port)
    except SystemExit:
        return
    if state is None:
        return
    try:
        matches = (
            int(state["pid"]) == pid
            and abs(float(state["create_time"]) - create_time) <= PROCESS_CREATE_TIME_TOLERANCE_SECONDS
        )
    except (KeyError, TypeError, ValueError):
        return
    if matches:
        remove_stale_state(port)


def write_state(process: psutil.Process, mode: str, port: int = DEFAULT_DASHBOARD_PORT) -> Path | None:
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    target = state_path(port)
    temporary = target.with_suffix(f".tmp-{os.getpid()}")
    payload = {
        "pid": process.pid,
        "create_time": process.create_time(),
        "python": sys.executable,
        "mode": mode,
        "port": port,
        "log": str(log_path(port)),
    }
    stop_request = None
    if mode == "foreground":
        stop_request = foreground_stop_path(process.pid, process.create_time())
        with suppress(FileNotFoundError):
            stop_request.unlink()
        payload["stop_request"] = str(stop_request)
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
    return stop_request


def reserve_start(port: int) -> psutil.Process | None:
    """Atomically reserve one management port or return its live launcher owner."""
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    target = state_path(port)
    owner = psutil.Process(os.getpid())
    payload = {
        "pid": owner.pid,
        "create_time": owner.create_time(),
        "python": sys.executable,
        "mode": "starting",
        "port": port,
        "log": str(log_path(port)),
    }
    temporary = target.with_name(f".{target.name}.reserve-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        with suppress(OSError):
            temporary.chmod(0o600)
        for _ in range(2):
            try:
                os.link(temporary, target)
            except FileExistsError:
                current = matching_process(load_state(port))
                if current is not None:
                    return current
                remove_stale_state(port)
                continue
            return None
        raise SystemExit(f"Unable to reserve launcher state for dashboard port {port}.")
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def management_port(arguments: list[str]) -> int:
    selected = str(DEFAULT_DASHBOARD_PORT)
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"-port", "--port"}:
            if index + 1 >= len(arguments):
                raise SystemExit(f"{argument} requires a port number.")
            selected = arguments[index + 1]
            index += 2
            continue
        if argument.startswith("-port=") or argument.startswith("--port="):
            selected = argument.partition("=")[2]
        index += 1
    try:
        port = int(selected)
    except ValueError as error:
        raise SystemExit(f"Dashboard port must be between {MIN_TCP_PORT} and {MAX_TCP_PORT}.") from error
    if not valid_port(port):
        raise SystemExit(f"Dashboard port must be between {MIN_TCP_PORT} and {MAX_TCP_PORT}.")
    return port


def informational(arguments: list[str]) -> bool:
    return any(argument in {"-h", "--help", "-v", "--version"} for argument in arguments)


def run_information_command(arguments: list[str]) -> int:
    report_environment()
    application = importlib.import_module("sbk_dashboard.main")
    result = application.main(arguments)
    return result if isinstance(result, int) else 0


def report_environment() -> None:
    print(
        f"Operating system: {platform.system()} {platform.release()} "
        f"({platform.machine()}; {platform.platform()})"
    )
    print(
        f"Python available: {platform.python_implementation()} {platform.python_version()} "
        f"at {sys.executable}"
    )
    if os.environ.get("VIRTUAL_ENV"):
        print(f"Environment: active virtual environment {os.environ['VIRTUAL_ENV']}")
    elif os.environ.get("CONDA_PREFIX"):
        print(f"Environment: active Conda environment {os.environ['CONDA_PREFIX']}")
    elif sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        print(f"Environment: virtual environment {sys.prefix}")
    else:
        print("Environment: system/PATH Python")
    runtime_kind = os.environ.get(BOOTSTRAP_RUNTIME_KIND_ENVIRONMENT)
    runtime_state = os.environ.get(BOOTSTRAP_RUNTIME_STATE_ENVIRONMENT)
    runtime_path = os.environ.get(BOOTSTRAP_RUNTIME_PATH_ENVIRONMENT)
    if runtime_kind:
        print(f"Bootstrap runtime: {runtime_kind}")
    elif getattr(sys, "frozen", False):
        print("Bootstrap runtime: standalone frozen runtime")
    if runtime_state:
        print(f"Runtime preparation: {runtime_state}")
    if runtime_path:
        print(f"Runtime location: {runtime_path}")
    portable_home = os.environ.get(PORTABLE_HOME_ENVIRONMENT)
    if portable_home:
        print(f"SBK Dashboard home: {portable_home}")
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
    os.environ[BOOTSTRAP_DIAGNOSTICS_REPORTED_ENVIRONMENT] = "1"


def foreground(arguments: list[str]) -> int:
    if informational(arguments):
        return run_information_command(arguments)
    port = management_port(arguments)
    report_environment()
    current = reserve_start(port)
    if current is not None:
        print(f"SBK Dashboard is already running on port {port} with PID {current.pid}.")
        return 0
    process = psutil.Process(os.getpid())
    process_created = process.create_time()
    try:
        stop_request = write_state(process, "foreground", port)
    except BaseException:
        remove_owned_state(process.pid, process_created, port)
        raise
    assert stop_request is not None
    monitor_stopping = threading.Event()

    def monitor_stop_request() -> None:
        while not monitor_stopping.wait(0.05):
            if stop_request.exists():
                with suppress(FileNotFoundError):
                    stop_request.unlink()
                signal.raise_signal(signal.SIGINT)
                return

    monitor = None
    if os.name == "nt":
        monitor = threading.Thread(target=monitor_stop_request, name="sbk-foreground-stop")
        monitor.start()
    print(f"Starting SBK Dashboard in the foreground with PID {process.pid}.")
    print("Press Ctrl+C to stop SBK Dashboard.")
    try:
        application = importlib.import_module("sbk_dashboard.main")
        application.main(arguments)
    finally:
        monitor_stopping.set()
        with suppress(FileNotFoundError):
            stop_request.unlink()
        if monitor is not None:
            monitor.join(timeout=1)
        remove_owned_state(process.pid, process_created, port)
    return 0


def start_background(arguments: list[str]) -> int:
    if informational(arguments):
        return run_information_command(arguments)
    port = management_port(arguments)
    report_environment()
    current = reserve_start(port)
    if current is not None:
        print(f"SBK Dashboard is already running on port {port} with PID {current.pid}.")
        return 0
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    parent = psutil.Process(os.getpid())
    startup_id = uuid.uuid4().hex
    marker = directory / f"startup-{startup_id}.authorized"
    started_marker = directory / f"startup-{startup_id}.started"
    try:
        child = subprocess.Popen(
            launcher_command(
                "_run",
                str(parent.pid),
                str(parent.create_time()),
                str(marker),
                str(started_marker),
                *arguments,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=os.name != "nt",
            creationflags=creation_flags,
            close_fds=True,
        )
    except BaseException:
        remove_owned_state(parent.pid, parent.create_time(), port)
        raise
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
                f"See {log_path(port)}."
            )
        try:
            process = psutil.Process(child.pid)
        except psutil.NoSuchProcess:
            raise SystemExit(
                f"SBK Dashboard exited during startup. See {log_path(port)}."
            ) from None
        write_state(process, "background", port)
        marker.touch(exist_ok=False)
        wait_for_background_start(child, started_marker, log_path(port))
    except BaseException:
        running_child = None
        if child.poll() is None:
            with suppress(psutil.NoSuchProcess):
                running_child = psutil.Process(child.pid)
        terminate_dashboard_group(child.pid, running_child)
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=FORCE_CLEANUP_SECONDS)
        remove_stale_state(port)
        for startup_marker in (marker, started_marker):
            with suppress(FileNotFoundError):
                startup_marker.unlink()
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    print(f"Started SBK Dashboard on port {port} with PID {child.pid} using {sys.executable}.")
    print(f"Log: {log_path(port)}")
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


def drain_child_output(child: subprocess.Popen[bytes], destination: Path) -> None:
    assert child.stdout is not None
    while True:
        chunk = child.stdout.read(READ_CHUNK_BYTES)
        if not chunk:
            return
        append_log(destination, chunk)


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
    port = management_port(arguments)
    instance_log_path = log_path(port)
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
            dashboard_command(arguments),
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
                launcher_command(
                    "_watch",
                    str(supervisor.pid),
                    str(supervisor_created),
                    str(child_process.pid),
                    str(child_process.create_time()),
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
                creationflags=watch_creation_flags,
                close_fds=True,
            )
            started_marker.touch(exist_ok=False)
        drain_child_output(child, instance_log_path)
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
        remove_owned_state(supervisor.pid, supervisor_created, port)
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


def request_stop(process: psutil.Process, mode: str, create_time: float) -> None:
    if os.name == "nt" and mode == "foreground":
        foreground_stop_path(process.pid, create_time).touch(exist_ok=True)
        return
    if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
        try:
            os.kill(process.pid, signal.CTRL_BREAK_EVENT)
            return
        except OSError:
            for child in process.children(recursive=True):
                try:
                    command = child.cmdline()
                    if "--internal-dashboard" in command or any(
                        command[index] == "-m" and command[index + 1] == "sbk_dashboard"
                        for index in range(len(command) - 1)
                    ):
                        child.terminate()
                        return
                except psutil.Error:
                    continue
            process.terminate()
            return
    process.terminate()


def stop_selection(arguments: list[str]) -> int | None:
    if not arguments:
        return None
    if len(arguments) == 1 and (
        arguments[0].startswith("-port=") or arguments[0].startswith("--port=")
    ):
        return management_port(arguments)
    if len(arguments) == 2 and arguments[0] in {"-port", "--port"}:
        return management_port(arguments)
    raise SystemExit("Usage: stop-sbk-dashboard [-port port]")


def stop_instance(port: int, state: dict[str, Any]) -> bool:
    process = matching_process(state)
    if process is None:
        remove_stale_state(port)
        return False
    try:
        descendants = process.children(recursive=True)
    except psutil.Error:
        descendants = []
    mode = str(state.get("mode", "background"))
    with suppress(psutil.Error):
        request_stop(process, mode, float(state["create_time"]))
    forced = wait_then_force([process, *descendants], stop_timeout())
    remove_owned_state(process.pid, float(state["create_time"]), port)
    suffix = " after bounded forceful cleanup" if forced else ""
    print(f"Stopped SBK Dashboard on port {port} with PID {process.pid}{suffix}.")
    return True


def stop(arguments: list[str] | None = None) -> int:
    supplied = [] if arguments is None else arguments
    if supplied in (["-h"], ["--help"]):
        print("Usage: stop-sbk-dashboard [-port port]")
        print("With no port, stops all instances started by the launcher.")
        return 0
    selected_port = stop_selection(supplied)
    states = load_states(selected_port)
    stopped = 0
    for port, state in states:
        if stop_instance(port, state):
            stopped += 1
    if stopped == 0:
        detail = "" if selected_port is None else f" on port {selected_port}"
        print(f"SBK Dashboard is not running{detail} (no matching launcher process).")
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
            "foreground|background|start [dashboard options...] | stop [-port port]"
        )
    if sys.argv[1] == "foreground":
        return foreground(sys.argv[2:])
    if sys.argv[1] in {"background", "start"}:
        return start_background(sys.argv[2:])
    return stop(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Install an immutable private runtime, then run the dashboard launcher."""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from python_requirement import ensure_supported  # noqa: E402

PROJECT_DIRECTORY = SCRIPT_DIRECTORY.parent
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from sbk_dashboard.contracts import (  # noqa: E402
    BOOTSTRAP_RUNTIME_KIND_ENVIRONMENT,
    BOOTSTRAP_RUNTIME_PATH_ENVIRONMENT,
    BOOTSTRAP_RUNTIME_STATE_ENVIRONMENT,
    PORTABLE_HOME_ENVIRONMENT,
)
from sbk_dashboard.layout import PortableHomeLayout  # noqa: E402
from sbk_dashboard.platforms import portable_platform_id  # noqa: E402

REQUIRED_MODULES = ("psutil", "sbk_dashboard")
LOCK_WAIT_SECONDS = 180.0
LOCK_STALE_SECONDS = 600.0
KEEP_RUNTIME_VERSIONS = 2
VERSION_PREFIX = 'VERSION = "'
FINGERPRINT_ROOT_FILES = ("pyproject.toml", "MANIFEST.in")
FINGERPRINT_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
WINDOWS_SYNCHRONIZE = 0x00100000
WINDOWS_WAIT_OBJECT_0 = 0x00000000
WINDOWS_ERROR_INVALID_PARAMETER = 87


@dataclass(frozen=True)
class PreparedEnvironment:
    """Interpreter selection and whether bootstrap created or reused it."""

    python: Path
    kind: str
    location: Path
    state: str


def dashboard_home(environment: dict[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    selected = values.get(PORTABLE_HOME_ENVIRONMENT, "").strip()
    try:
        return PortableHomeLayout.from_value(selected).root
    except ValueError as error:
        raise SystemExit(f"{error}.") from error


def project_version(project_directory: Path) -> str:
    source = project_directory / "src" / "sbk_dashboard" / "version.py"
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.startswith(VERSION_PREFIX) and line.endswith('"'):
            return line[len(VERSION_PREFIX) : -1]
    raise SystemExit(f"Unable to read the SBK Dashboard version from {source}.")


def platform_id() -> str:
    try:
        return portable_platform_id()
    except ValueError as error:
        raise SystemExit(f"{error}.") from error


def source_fingerprint(project_directory: Path) -> str:
    digest = hashlib.sha256()
    paths = [project_directory / name for name in FINGERPRINT_ROOT_FILES]
    for directory in (project_directory / "src", project_directory / "scripts"):
        paths.extend(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix not in FINGERPRINT_EXCLUDED_SUFFIXES
            and "__pycache__" not in path.parts
            and not any(part.endswith(".egg-info") for part in path.parts)
        )
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.relative_to(project_directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def runtime_directory(home: Path, version: str, fingerprint: str) -> Path:
    return home / "app" / version / platform_id() / fingerprint


def runtime_python(runtime: Path) -> Path:
    return runtime / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def installed_marker(runtime: Path) -> Path:
    return runtime / "installed.json"


def executable_exists(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def environment_is_active() -> bool:
    return bool(os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX"))


def missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def run_checked(command: list[str], failure: str, environment: dict[str, str] | None = None) -> None:
    try:
        subprocess.run(command, check=True, env=environment)
    except (OSError, subprocess.CalledProcessError) as error:
        rendered = subprocess.list2cmdline(command)
        raise SystemExit(f"{failure}\nFailed command: {rendered}\nDetails: {error}") from error


def windows_process_alive(pid: int, kernel32: Any | None = None) -> bool:
    """Check a Windows PID without sending a signal or requiring psutil."""
    if kernel32 is None:
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            return True
        try:
            kernel32 = loader("kernel32", use_last_error=True)
        except OSError:
            return True
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(WINDOWS_SYNCHRONIZE, False, pid)
    if not handle:
        get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
        return int(get_last_error()) != WINDOWS_ERROR_INVALID_PARAMETER
    try:
        return int(kernel32.WaitForSingleObject(handle, 0)) != WINDOWS_WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class InstallLock:
    """Bounded cross-platform exclusive-file installation lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.token = uuid.uuid4().hex

    def __enter__(self) -> InstallLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._remove_stale() or time.monotonic() < deadline:
                    time.sleep(0.2)
                    continue
                raise SystemExit(f"Timed out waiting for portable runtime installation lock {self.path}.") from None
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"pid": os.getpid(), "created": time.time(), "token": self.token}, output)
                output.flush()
                os.fsync(output.fileno())
            return self

    def _remove_stale(self) -> bool:
        try:
            value: Any = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(value["pid"])
            created = float(value["created"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return False
        if time.time() - created <= LOCK_STALE_SECONDS or process_alive(pid):
            return False
        with suppress(FileNotFoundError):
            self.path.unlink()
        return True

    def __exit__(self, *_error: object) -> None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("token") == self.token:
                self.path.unlink()
        except (OSError, json.JSONDecodeError):
            return


def atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def runtime_valid(runtime: Path, version: str, fingerprint: str) -> bool:
    try:
        marker = json.loads(installed_marker(runtime).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        marker.get("version") == version
        and marker.get("fingerprint") == fingerprint
        and executable_exists(runtime_python(runtime))
    )


def move_directory(source: Path, destination: Path) -> None:
    """Atomically move one directory only when the destination is absent."""
    if destination.exists():
        raise FileExistsError(f"Runtime promotion destination already exists: {destination}")
    os.rename(source, destination)


def install_application(python: Path, project_directory: Path, home: Path) -> None:
    environment = dict(os.environ)
    environment["PIP_CACHE_DIR"] = str(home / "cache" / "pip")
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    pip_check = subprocess.run(
        [str(python), "-m", "pip", "--version"],
        check=False,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if pip_check.returncode != 0:
        run_checked(
            [str(python), "-m", "ensurepip", "--upgrade"],
            "The selected Python environment has no pip module and it could not be installed.",
            environment,
        )
    run_checked(
        [str(python), "-m", "pip", "install", str(project_directory)],
        "Unable to install SBK Dashboard dependencies. Check package-index access and retry.",
        environment,
    )


def install_private_runtime(
    project_directory: Path, home: Path, version: str, fingerprint: str, force: bool = False
) -> PreparedEnvironment:
    selected = runtime_directory(home, version, fingerprint)
    lock = home / "launcher" / "locks" / f"runtime-{version}-{platform_id()}.lock"
    with InstallLock(lock):
        selected.parent.mkdir(parents=True, exist_ok=True)
        abandoned_backups = sorted(selected.parent.glob(f".{selected.name}.backup-*"))
        if not selected.exists() and abandoned_backups:
            move_directory(abandoned_backups.pop(), selected)
        for abandoned in abandoned_backups:
            shutil.rmtree(abandoned, ignore_errors=True)
        for abandoned in selected.parent.glob(f".{selected.name}.staging-*"):
            shutil.rmtree(abandoned, ignore_errors=True)
        if runtime_valid(selected, version, fingerprint) and not force:
            return PreparedEnvironment(
                runtime_python(selected), "private virtual environment", selected, "saved environment reused"
            )
        staging = selected.with_name(f".{selected.name}.staging-{uuid.uuid4().hex}")
        backup = selected.with_name(f".{selected.name}.backup-{uuid.uuid4().hex}")
        try:
            print(f"Creating private SBK Dashboard runtime {selected}", flush=True)
            run_checked(
                [sys.executable, "-m", "venv", str(staging / "venv")],
                "Unable to create the private runtime. Install Python with the venv module and retry.",
            )
            install_application(runtime_python(staging), project_directory, home)
            atomic_json(
                installed_marker(staging),
                {
                    "version": version,
                    "fingerprint": fingerprint,
                    "platform": platform_id(),
                    "created": time.time(),
                },
            )
            if selected.exists():
                move_directory(selected, backup)
            try:
                move_directory(staging, selected)
            except BaseException:
                if backup.exists() and not selected.exists():
                    move_directory(backup, selected)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            atomic_json(
                home / "current.json",
                {"version": version, "fingerprint": fingerprint, "platform": platform_id()},
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if backup.exists() and not selected.exists():
                move_directory(backup, selected)
        prune_runtimes(selected.parent, selected)
        state = "environment repaired" if force else "fresh environment created"
        return PreparedEnvironment(runtime_python(selected), "private virtual environment", selected, state)


def prune_runtimes(parent: Path, current: Path) -> None:
    candidates = sorted(
        (path for path in parent.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    keep = {current, *candidates[:KEEP_RUNTIME_VERSIONS]}
    for candidate in candidates:
        if candidate not in keep:
            shutil.rmtree(candidate, ignore_errors=True)


def active_environment_marker(
    home: Path, project_directory: Path, fingerprint: str | None = None
) -> Path:
    interpreter = hashlib.sha256(str(Path(sys.executable).resolve()).encode()).hexdigest()[:16]
    selected_fingerprint = fingerprint or source_fingerprint(project_directory)
    return home / "app" / "active" / interpreter / f"{selected_fingerprint}.json"


def active_environment_valid(marker: Path, version: str, fingerprint: str) -> bool:
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        value.get("version") == version
        and value.get("fingerprint") == fingerprint
        and value.get("python") == str(Path(sys.executable).resolve())
    )


def prepare_active_environment(
    project_directory: Path, home: Path, version: str, force: bool = False
) -> bool:
    missing = missing_modules()
    fingerprint = source_fingerprint(project_directory)
    marker = active_environment_marker(home, project_directory, fingerprint)
    if not missing and active_environment_valid(marker, version, fingerprint) and not force:
        return False
    reason = f"missing: {', '.join(missing)}" if missing else "checkout changed or was not prepared"
    print(
        f"Installing SBK Dashboard into active environment {sys.executable} ({reason})",
        flush=True,
    )
    install_application(Path(sys.executable), project_directory, home)
    importlib.invalidate_caches()
    remaining = missing_modules()
    if remaining:
        raise SystemExit("Required modules remain unavailable: " + ", ".join(remaining))
    atomic_json(
        marker,
        {
            "version": version,
            "fingerprint": fingerprint,
            "python": str(Path(sys.executable).resolve()),
            "created": time.time(),
        },
    )
    for old_marker in marker.parent.glob("*.json"):
        if old_marker != marker:
            with suppress(OSError):
                old_marker.unlink()
    return True


def active_environment_kind() -> str:
    if os.environ.get("VIRTUAL_ENV"):
        return "active virtual environment"
    if os.environ.get("CONDA_PREFIX"):
        return "active Conda environment"
    return "active Python environment"


def launch(
    prepared: PreparedEnvironment, script_directory: Path, selected: list[str], home: Path
) -> None:
    os.environ[PORTABLE_HOME_ENVIRONMENT] = str(home)
    os.environ.setdefault("PIP_CACHE_DIR", str(home / "cache" / "pip"))
    os.environ[BOOTSTRAP_RUNTIME_KIND_ENVIRONMENT] = prepared.kind
    os.environ[BOOTSTRAP_RUNTIME_STATE_ENVIRONMENT] = prepared.state
    os.environ[BOOTSTRAP_RUNTIME_PATH_ENVIRONMENT] = str(prepared.location)
    launcher = script_directory / "sbk_dashboard_launcher.py"
    os.execv(str(prepared.python), [str(prepared.python), str(launcher), *selected])


def main(arguments: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if arguments is None else arguments)
    if not selected or selected[0] not in {"foreground", "background", "stop", "repair"}:
        raise SystemExit("Usage: sbk_dashboard_bootstrap.py {foreground|background|stop|repair} [dashboard options]")
    ensure_supported()
    script_directory = Path(__file__).resolve().parent
    project_directory = script_directory.parent
    home = dashboard_home()
    version = project_version(project_directory)
    fingerprint = source_fingerprint(project_directory)
    if environment_is_active():
        force = selected[0] == "repair"
        prepared_now = prepare_active_environment(project_directory, home, version, force=force)
        state = "environment repaired" if force else (
            "fresh environment prepared" if prepared_now else "saved environment reused"
        )
        prepared = PreparedEnvironment(
            Path(sys.executable), active_environment_kind(), Path(sys.prefix), state
        )
        if selected[0] == "repair":
            print(f"Repaired SBK Dashboard {version} in active environment {sys.executable}")
            return 0
        launch(prepared, script_directory, selected, home)
        return 0
    prepared = install_private_runtime(
        project_directory, home, version, fingerprint, force=selected[0] == "repair"
    )
    if selected[0] == "repair":
        print(f"Repaired SBK Dashboard {version} runtime at {prepared.location}")
        return 0
    launch(prepared, script_directory, selected, home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

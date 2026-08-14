#!/usr/bin/env python3
"""Prepare a Python environment, then run the cross-platform dashboard launcher."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

MINIMUM_PYTHON = (3, 10)
REQUIRED_MODULES = ("psutil", "sbk_dashboard")


def project_python(project_directory: Path) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return project_directory / ".venv" / directory / executable


def executable_exists(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def environment_is_active() -> bool:
    return bool(os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX"))


def missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def run_checked(command: list[str], failure: str) -> None:
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        rendered = subprocess.list2cmdline(command)
        raise SystemExit(f"{failure}\nFailed command: {rendered}\nDetails: {error}") from error


def ensure_project_environment(project_directory: Path, arguments: list[str]) -> None:
    selected = project_python(project_directory)
    print(f"Creating project virtual environment {selected.parent.parent}", flush=True)
    run_checked(
        [sys.executable, "-m", "venv", str(selected.parent.parent)],
        "Unable to create the project virtual environment. Install Python with the venv module and retry.",
    )
    if not selected.is_file():
        raise SystemExit(f"Virtual environment creation did not provide Python at {selected}.")
    os.execv(str(selected), [str(selected), str(Path(__file__).resolve()), *arguments])


def ensure_application(project_directory: Path) -> None:
    missing = missing_modules()
    if not missing:
        return
    print(
        f"Installing SBK Dashboard and required packages into {sys.executable} "
        f"(missing: {', '.join(missing)})",
        flush=True,
    )
    pip_check = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if pip_check.returncode != 0:
        print(f"Installing pip into {sys.executable}", flush=True)
        run_checked(
            [sys.executable, "-m", "ensurepip", "--upgrade"],
            "The selected Python environment has no pip module and it could not be installed.",
        )
    run_checked(
        [sys.executable, "-m", "pip", "install", str(project_directory)],
        "Unable to install SBK Dashboard dependencies. Check pip and network/package-index access, then retry.",
    )
    importlib.invalidate_caches()
    remaining = missing_modules()
    if remaining:
        raise SystemExit(
            "Installation completed but required modules are still unavailable: " + ", ".join(remaining)
        )


def main(arguments: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if arguments is None else arguments)
    if not selected or selected[0] not in {"foreground", "background"}:
        raise SystemExit("Usage: sbk_dashboard_bootstrap.py {foreground|background} [dashboard options]")
    if sys.version_info < MINIMUM_PYTHON:
        raise SystemExit(
            f"Python 3.10 or newer is required; selected interpreter reports {sys.version.split()[0]}."
        )
    script_directory = Path(__file__).resolve().parent
    project_directory = script_directory.parent
    selected_project_python = project_python(project_directory)
    if not environment_is_active() and not executable_exists(selected_project_python):
        ensure_project_environment(project_directory, selected)
    ensure_application(project_directory)
    launcher = script_directory / "sbk_dashboard_launcher.py"
    os.execv(sys.executable, [sys.executable, str(launcher), *selected])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

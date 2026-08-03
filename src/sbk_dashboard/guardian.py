"""Internal native-process guardian for hard control-plane termination."""

from __future__ import annotations

import argparse
import logging
import subprocess
from pathlib import Path

import psutil

from sbk_dashboard.files import atomic_json
from sbk_dashboard.processes import _terminate_psutil_tree

PARENT_POLL_SECONDS = 0.25
LOGGER = logging.getLogger(__name__)


def _parent_alive(pid: int, started: float) -> bool:
    try:
        parent = psutil.Process(pid)
        return (
            parent.is_running()
            and parent.status() != psutil.STATUS_ZOMBIE
            and abs(parent.create_time() - started) <= 0.01
        )
    except psutil.Error:
        return False


def guard(parent_pid: int, parent_started: float, state_path: Path, name: str, command: list[str]) -> int:
    """Run one native process and terminate its tree if the validated parent disappears."""
    if not command:
        raise ValueError("Guardian native command must not be empty")
    if not _parent_alive(parent_pid, parent_started):
        return 1
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        LOGGER.error("Unable to launch guarded %s: %s", name, error)
        return 127
    try:
        atomic_json(state_path, {"pid": process.pid})
        while True:
            try:
                code = process.wait(timeout=PARENT_POLL_SECONDS)
                return code if 0 <= code <= 255 else 1
            except subprocess.TimeoutExpired:
                if _parent_alive(parent_pid, parent_started):
                    continue
                try:
                    _terminate_psutil_tree(psutil.Process(process.pid), name)
                except (OSError, psutil.Error) as error:
                    LOGGER.error("Unable to clean orphaned %s: %s", name, error)
                    return 1
                return 0
    finally:
        state_path.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(add_help=False)
    result.add_argument("--parent-pid", type=int, required=True)
    result.add_argument("--parent-started", type=float, required=True)
    result.add_argument("--pid-file", type=Path, required=True)
    result.add_argument("--name", required=True)
    result.add_argument("command", nargs=argparse.REMAINDER)
    return result


def main(arguments: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    namespace = parser().parse_args(arguments)
    command = list(namespace.command)
    if command and command[0] == "--":
        command.pop(0)
    try:
        return guard(
            namespace.parent_pid,
            namespace.parent_started,
            namespace.pid_file,
            namespace.name,
            command,
        )
    except ValueError as error:
        LOGGER.error("%s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

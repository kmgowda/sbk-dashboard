#!/usr/bin/env python3
"""Entry point for self-contained portable SBK Dashboard executables."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def portable_home() -> Path:
    selected = os.environ.get("SBK_DASHBOARD_HOME", "").strip()
    home = Path(selected).expanduser() if selected else Path.home() / ".sbk-dashboard"
    resolved = home.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise SystemExit("SBK_DASHBOARD_HOME must be a dedicated subdirectory, not a filesystem or home root.")
    return resolved


def main(arguments: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if arguments is None else arguments)
    os.environ["SBK_DASHBOARD_HOME"] = str(portable_home())
    if selected and selected[0] == "--internal-guardian":
        from sbk_dashboard.guardian import main as guardian_main

        return guardian_main(selected[1:])
    from sbk_dashboard.main import main as application_main

    application_main(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

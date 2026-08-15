#!/usr/bin/env python3
"""Entry point for self-contained portable SBK Dashboard executables."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sbk_dashboard.contracts import PORTABLE_HOME_ENVIRONMENT
from sbk_dashboard.layout import PortableHomeLayout


def portable_home() -> Path:
    selected = os.environ.get(PORTABLE_HOME_ENVIRONMENT, "").strip()
    try:
        return PortableHomeLayout.from_value(selected).root
    except ValueError as error:
        raise SystemExit(f"{error}.") from error


def main(arguments: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if arguments is None else arguments)
    os.environ[PORTABLE_HOME_ENVIRONMENT] = str(portable_home())
    if selected and selected[0] == "--internal-guardian":
        from sbk_dashboard.guardian import main as guardian_main

        return guardian_main(selected[1:])
    from sbk_dashboard.main import main as application_main

    application_main(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

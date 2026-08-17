#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Entry point for self-contained portable SBK Dashboard executables."""

from __future__ import annotations

import os
import runpy
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


def launcher_main(arguments: list[str]) -> int:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    launcher = bundle_root / "sbk_dashboard_launcher.py"
    if not launcher.is_file():
        raise SystemExit(f"Portable launcher resource is missing: {launcher}")
    namespace = runpy.run_path(str(launcher), run_name="_sbk_dashboard_portable_launcher")
    previous = sys.argv
    try:
        sys.argv = [str(launcher), *arguments]
        result = namespace["main"]()
    finally:
        sys.argv = previous
    return result if isinstance(result, int) else 0


def main(arguments: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if arguments is None else arguments)
    os.environ[PORTABLE_HOME_ENVIRONMENT] = str(portable_home())
    if selected and selected[0] == "--internal-guardian":
        from sbk_dashboard.guardian import main as guardian_main

        return guardian_main(selected[1:])
    if selected and selected[0] == "--internal-dashboard":
        from sbk_dashboard.main import main as application_main

        application_main(selected[1:])
        return 0
    if selected and selected[0] == "--internal-launcher":
        return launcher_main(selected[1:])

    command = selected[0] if selected else "foreground"
    if command == "repair":
        raise SystemExit(
            "Repair a standalone runtime through the source-checkout root launcher, "
            "or replace it from its verified release archive."
        )
    if command in {"start", "foreground", "background", "stop"}:
        remaining = selected[1:]
    else:
        command = "foreground"
        remaining = selected
    return launcher_main([command, *remaining])


if __name__ == "__main__":
    raise SystemExit(main())

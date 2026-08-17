#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Check or update current-release references from version.py."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = r"\d+\.\d+\.\d+\.\d+"
TARGETS: dict[str, tuple[str, ...]] = {
    "Dockerfile": (rf"(?m)(^ARG APPLICATION_VERSION=){VERSION_PATTERN}()$",),
    "compose.yaml": (rf"(kmgowda/sbk-dashboard:){VERSION_PATTERN}()",),
    "compose.dev.yaml": (rf"(sbk-dashboard:){VERSION_PATTERN}()",),
    "README.md": (
        rf"(The current release is `){VERSION_PATTERN}(`)",
        rf"(kmgowda/sbk-dashboard:){VERSION_PATTERN}()",
    ),
    "docs/DOCKER.md": (rf"(sbk-dashboard:){VERSION_PATTERN}()",),
    "docs/DOCKER_HUB.md": (
        rf"(sbk-dashboard:){VERSION_PATTERN}()",
        rf"(version output should be `){VERSION_PATTERN}(`)",
    ),
}


def package_version() -> str:
    text = (ROOT / "src/sbk_dashboard/version.py").read_text(encoding="utf-8")
    match = re.search(rf'(?m)^VERSION = "({VERSION_PATTERN})"$', text)
    if match is None:
        raise ValueError("version.py does not contain a supported VERSION assignment")
    return match.group(1)


def synchronize(*, write: bool) -> list[str]:
    version = package_version()
    mismatches: list[str] = []
    for relative, patterns in TARGETS.items():
        path = ROOT / relative
        original = path.read_text(encoding="utf-8")
        updated = original
        for pattern in patterns:
            updated, count = re.subn(pattern, rf"\g<1>{version}\g<2>", updated)
            if count == 0:
                raise ValueError(f"Release metadata pattern was not found in {relative}: {pattern}")
        if updated != original:
            mismatches.append(relative)
            if write:
                path.write_text(updated, encoding="utf-8")
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="update mismatched current-release references")
    arguments = parser.parse_args()
    mismatches = synchronize(write=arguments.write)
    if mismatches and not arguments.write:
        print("Release metadata is out of sync: " + ", ".join(mismatches))
        print("Run: python scripts/sync_release_metadata.py --write")
        return 1
    if mismatches:
        print("Updated release metadata: " + ", ".join(mismatches))
    else:
        print("Release metadata is synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

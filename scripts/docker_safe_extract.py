#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Safely extract a single-root TAR archive while removing its root directory."""

from __future__ import annotations

import copy
import sys
import tarfile
from pathlib import Path, PurePosixPath


def extract(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        roots: set[str] = set()
        stripped: list[tarfile.TarInfo] = []
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe archive member: {member.name!r}")
            parts = tuple(part for part in path.parts if part not in {"", "."})
            if not parts:
                continue
            roots.add(parts[0])
            if len(parts) == 1:
                continue
            item = copy.copy(member)
            item.name = PurePosixPath(*parts[1:]).as_posix()
            stripped.append(item)
        if len(roots) != 1:
            raise ValueError(f"archive must contain one root directory, found {sorted(roots)!r}")
        archive.extractall(destination, members=stripped, filter="data")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"Usage: {Path(sys.argv[0]).name} ARCHIVE DESTINATION")
    extract(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()

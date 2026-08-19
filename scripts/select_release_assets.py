#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Print only missing GitHub Release assets after verifying immutable existing assets."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_release_manifest import sha256  # noqa: E402
from release import DEFAULT_REPOSITORY, GitHubClient, ReleaseError  # noqa: E402
from release_contract import MAX_RELEASE_ASSETS, release_tag, required_release_assets  # noqa: E402
from sync_release_metadata import package_version  # noqa: E402


class AssetSelectionError(RuntimeError):
    """Existing release state is unsafe for an immutable incremental upload."""


def missing_assets(directory: Path, version: str, release: dict[str, Any]) -> list[Path]:
    """Return missing paths and reject any conflicting existing release asset."""
    directory = directory.resolve()
    expected_names = required_release_assets(version)
    actual_names = {path.name for path in directory.iterdir()}
    if actual_names != set(expected_names):
        raise AssetSelectionError("Release directory does not contain the exact contracted asset set")
    remote_assets = release.get("assets", [])
    if not isinstance(remote_assets, list) or len(remote_assets) > MAX_RELEASE_ASSETS:
        raise AssetSelectionError("GitHub Release returned an invalid or oversized asset list")
    existing: dict[str, dict[str, Any]] = {}
    for asset in remote_assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise AssetSelectionError("GitHub Release returned invalid asset metadata")
        name = asset["name"]
        if name in existing:
            raise AssetSelectionError(f"GitHub Release contains duplicate asset {name!r}")
        existing[name] = asset
    selected: list[Path] = []
    for name in expected_names:
        path = directory / name
        digest = sha256(path)
        remote = existing.get(name)
        if remote is None:
            selected.append(path)
            continue
        if remote.get("size") != path.stat().st_size or remote.get("digest") != f"sha256:{digest}":
            raise AssetSelectionError(f"Existing GitHub Release asset differs from local file: {name}")
        print(f"Keeping identical existing release asset: {name}", file=sys.stderr)
    return selected


def main(arguments: list[str] | None = None) -> int:
    """Select missing immutable assets for a bounded GitHub Actions upload."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    selected = parser.parse_args(arguments)
    version = package_version()
    if selected.tag != release_tag(version):
        raise SystemExit(f"error: tag {selected.tag!r} does not match version {version!r}")
    if selected.repository != DEFAULT_REPOSITORY:
        raise SystemExit(f"error: repository must be {DEFAULT_REPOSITORY!r}")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("error: GITHUB_TOKEN is required to inspect release assets")
    release = GitHubClient(selected.repository, token).release(selected.tag)
    if release is None:
        raise SystemExit(f"error: GitHub Release {selected.tag!r} was not found")
    try:
        for path in missing_assets(selected.directory, version, release):
            print(path)
    except (OSError, UnicodeError, ValueError, ReleaseError, AssetSelectionError) as error:
        raise SystemExit(f"error: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

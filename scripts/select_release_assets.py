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
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_release_manifest import sha256  # noqa: E402
from release import (  # noqa: E402
    DEFAULT_REPOSITORY,
    GitHubClient,
    RateLimitError,
    ReleaseError,
    bounded_sleep,
    rate_limit_delay,
)
from release_contract import MAX_RELEASE_ASSETS, release_tag, required_release_assets  # noqa: E402
from sync_release_metadata import package_version  # noqa: E402


class AssetSelectionError(RuntimeError):
    """Existing release state is unsafe for an immutable incremental upload."""


class PendingAssetError(AssetSelectionError):
    """GitHub has not finished processing one otherwise matching release asset."""


ASSET_PROPAGATION_TIMEOUT_SECONDS = 5 * 60
ASSET_PROPAGATION_POLL_SECONDS = 5.0


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
        state = remote.get("state")
        remote_digest = remote.get("digest")
        if (isinstance(state, str) and state != "uploaded") or remote_digest is None:
            raise PendingAssetError(f"GitHub is still processing release asset: {name}")
        if not isinstance(remote_digest, str):
            raise AssetSelectionError(f"GitHub Release returned invalid digest metadata: {name}")
        if remote.get("size") != path.stat().st_size or remote_digest != f"sha256:{digest}":
            raise AssetSelectionError(f"Existing GitHub Release asset differs from local file: {name}")
        print(f"Keeping identical existing release asset: {name}", file=sys.stderr)
    return selected


def wait_for_missing_assets(
    github: GitHubClient,
    directory: Path,
    version: str,
    tag: str,
    *,
    timeout_seconds: float = ASSET_PROPAGATION_TIMEOUT_SECONDS,
    poll_seconds: float = ASSET_PROPAGATION_POLL_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> list[Path]:
    """Wait a bounded period for existing GitHub asset digests, then select missing files."""
    deadline = monotonic() + timeout_seconds
    delay = poll_seconds
    while monotonic() < deadline:
        try:
            release = github.release(tag)
            delay = poll_seconds
        except RateLimitError as error:
            delay = rate_limit_delay(delay, poll_seconds, error.retry_after)
            print("GitHub rate-limited asset inspection; applying bounded backoff...", file=sys.stderr)
            if not bounded_sleep(deadline, delay, sleeper=sleeper, monotonic=monotonic):
                break
            continue
        if release is None:
            raise AssetSelectionError(f"GitHub Release {tag!r} was not found")
        try:
            return missing_assets(directory, version, release)
        except PendingAssetError as error:
            print(f"{error}; waiting for GitHub asset metadata...", file=sys.stderr)
        if not bounded_sleep(deadline, poll_seconds, sleeper=sleeper, monotonic=monotonic):
            break
    raise AssetSelectionError("Timed out waiting for GitHub release asset metadata")


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
    try:
        github = GitHubClient(selected.repository, token)
        for path in wait_for_missing_assets(github, selected.directory, version, selected.tag):
            print(path)
    except (OSError, UnicodeError, ValueError, ReleaseError, AssetSelectionError) as error:
        raise SystemExit(f"error: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

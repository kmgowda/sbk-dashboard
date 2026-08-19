#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Shared names and bounds for SBK Dashboard GitHub release delivery."""

from __future__ import annotations

import re

VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+\.\d+")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
PORTABLE_ARCHIVE_SUFFIXES = (
    "linux-amd64.tar.gz",
    "macos-arm64.tar.gz",
    "windows-amd64.zip",
)
CHECKSUMS_FILENAME = "SHA256SUMS"
RELEASE_MANIFEST_FILENAME = "release-manifest.json"
RELEASE_MANIFEST_SCHEMA_VERSION = 1
HASH_CHUNK_BYTES = 1024 * 1024
SHA256_HEX_CHARACTERS = 64
CHECKSUM_FILE_FIELDS = 2
MAX_CHECKSUM_FILE_BYTES = 1024
MAX_RELEASE_ASSET_BYTES = 2 * 1024 * 1024 * 1024
MAX_RELEASE_ASSETS = 32


def validate_version(version: str) -> str:
    """Return a valid four-component SBK Dashboard version."""
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError(f"Unsupported release version: {version!r}")
    return version


def release_tag(version: str) -> str:
    """Return the canonical annotated Git tag for a release version."""
    return f"v{validate_version(version)}"


def required_build_artifacts(version: str) -> tuple[str, ...]:
    """Return artifacts that platform and package jobs must produce before publication."""
    validate_version(version)
    portable: list[str] = []
    for suffix in PORTABLE_ARCHIVE_SUFFIXES:
        archive = f"sbk-dashboard-{version}-{suffix}"
        portable.extend((archive, f"{archive}.sha256"))
    return (
        f"sbk_dashboard-{version}-py3-none-any.whl",
        f"sbk_dashboard-{version}.tar.gz",
        *portable,
    )


def required_release_assets(version: str) -> tuple[str, ...]:
    """Return the complete explicit asset set expected on the GitHub Release."""
    return (*required_build_artifacts(version), CHECKSUMS_FILENAME, RELEASE_MANIFEST_FILENAME)

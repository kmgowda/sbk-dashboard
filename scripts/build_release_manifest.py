#!/usr/bin/env python3
# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Validate release artifacts and generate checksums plus a bounded manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from release_contract import (  # noqa: E402
    CHECKSUM_FILE_FIELDS,
    CHECKSUMS_FILENAME,
    COMMIT_PATTERN,
    HASH_CHUNK_BYTES,
    MAX_CHECKSUM_FILE_BYTES,
    MAX_RELEASE_ASSET_BYTES,
    MAX_RELEASE_ASSETS,
    PORTABLE_ARCHIVE_SUFFIXES,
    RELEASE_MANIFEST_FILENAME,
    RELEASE_MANIFEST_SCHEMA_VERSION,
    SHA256_HEX_CHARACTERS,
    release_tag,
    required_build_artifacts,
    validate_version,
)


def sha256(path: Path) -> str:
    """Return the SHA-256 digest for one bounded regular file."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Release asset must be a regular file: {path.name}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_RELEASE_ASSET_BYTES:
        raise ValueError(f"Release asset has invalid size {size}: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(directory: Path, version: str, tag: str, commit: str) -> dict[str, object]:
    """Validate the exact build outputs and write deterministic checksum metadata."""
    version = validate_version(version)
    if tag != release_tag(version):
        raise ValueError(f"Release tag {tag!r} does not match version {version!r}")
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise ValueError("Release commit must be a complete lowercase Git SHA")
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"Release directory does not exist: {directory}")
    files = sorted(
        path
        for path in directory.iterdir()
        if path.name not in {CHECKSUMS_FILENAME, RELEASE_MANIFEST_FILENAME}
    )
    if len(files) > MAX_RELEASE_ASSETS:
        raise ValueError(f"Release directory contains more than {MAX_RELEASE_ASSETS} assets")
    expected = set(required_build_artifacts(version))
    actual = {path.name for path in files}
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(f"Release artifact mismatch; missing={missing}, unexpected={unexpected}")
    artifacts = [
        {"name": path.name, "size": path.stat().st_size, "sha256": sha256(path)} for path in files
    ]
    digests = {str(artifact["name"]): str(artifact["sha256"]) for artifact in artifacts}
    for suffix in PORTABLE_ARCHIVE_SUFFIXES:
        archive_name = f"sbk-dashboard-{version}-{suffix}"
        checksum_path = directory / f"{archive_name}.sha256"
        if checksum_path.stat().st_size > MAX_CHECKSUM_FILE_BYTES:
            raise ValueError(f"Portable checksum file is too large: {checksum_path.name}")
        fields = checksum_path.read_text(encoding="ascii").split()
        if (
            len(fields) != CHECKSUM_FILE_FIELDS
            or len(fields[0]) != SHA256_HEX_CHARACTERS
            or fields[1] != archive_name
            or fields[0].lower() != digests[archive_name]
        ):
            raise ValueError(f"Portable checksum does not match {archive_name}")
    checksums = "".join(f"{artifact['sha256']}  {artifact['name']}\n" for artifact in artifacts)
    (directory / CHECKSUMS_FILENAME).write_text(checksums, encoding="ascii")
    manifest: dict[str, object] = {
        "schemaVersion": RELEASE_MANIFEST_SCHEMA_VERSION,
        "applicationVersion": version,
        "tag": tag,
        "commit": commit,
        "artifacts": artifacts,
    }
    (directory / RELEASE_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def application_version(root: Path) -> str:
    """Read the application version without importing the runtime package."""
    namespace: dict[str, object] = {}
    exec((root / "src/sbk_dashboard/version.py").read_text(encoding="utf-8"), namespace)
    return validate_version(str(namespace["VERSION"]))


def main(arguments: list[str] | None = None) -> int:
    """Build release metadata for the artifacts downloaded by release CI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    selected = parser.parse_args(arguments)
    version = application_version(Path(__file__).resolve().parents[1])
    build_manifest(selected.directory, version, selected.tag, selected.commit)
    print(f"Prepared {len(required_build_artifacts(version))} release artifacts for {selected.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

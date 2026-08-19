# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Atomic installation of the bundled frontend-only Grafana comparison app."""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from contextlib import suppress
from importlib.resources import files
from pathlib import Path

from sbk_dashboard.provisioning import COMPARISON_APP_PLUGIN_ID

LEGACY_COMPARISON_APP_PLUGIN_IDS = ("kmg-sbkcomparison-app",)

if sys.version_info >= (3, 11):
    from importlib.resources.abc import Traversable
else:
    from importlib.abc import Traversable


def install_comparison_plugin(plugin_root: Path) -> Path:
    """Install the immutable packaged plugin without exposing a partially copied directory."""
    source = files("sbk_dashboard")
    for part in ("resources", "grafana", "plugins", COMPARISON_APP_PLUGIN_ID):
        source = source.joinpath(part)
    if not source.is_dir():
        raise OSError("Bundled Grafana comparison plugin is unavailable")
    plugin_root.mkdir(parents=True, exist_ok=True)
    destination = plugin_root / COMPARISON_APP_PLUGIN_ID
    token = uuid.uuid4().hex
    staging = plugin_root / f".{COMPARISON_APP_PLUGIN_ID}.{token}.staging"
    previous = plugin_root / f".{COMPARISON_APP_PLUGIN_ID}.{token}.previous"
    try:
        _copy_tree(source, staging)
        if destination.exists():
            os.replace(destination, previous)
        try:
            os.replace(staging, destination)
        except BaseException:
            if previous.exists() and not destination.exists():
                os.replace(previous, destination)
            raise
        shutil.rmtree(previous, ignore_errors=True)
        for legacy_plugin_id in LEGACY_COMPARISON_APP_PLUGIN_IDS:
            legacy = plugin_root / legacy_plugin_id
            with suppress(OSError):
                if legacy.is_symlink() or legacy.is_file():
                    legacy.unlink()
                elif legacy.is_dir():
                    shutil.rmtree(legacy)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        with suppress(OSError):
            if previous.exists() and not destination.exists():
                os.replace(previous, destination)
        raise
    return destination


def _copy_tree(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for child in source.iterdir():
        selected = destination / child.name
        if child.is_dir():
            _copy_tree(child, selected)
        elif child.is_file():
            selected.write_bytes(child.read_bytes())
        else:
            raise OSError(f"Unsupported packaged plugin entry: {child.name}")

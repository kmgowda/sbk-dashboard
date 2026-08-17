# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Shared runtime and portable-release platform normalization."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass

OPERATING_SYSTEM_ALIASES = {
    "darwin": "macos",
    "linux": "linux",
    "win32": "windows",
}
X86_64_ALIASES = frozenset({"amd64", "x86_64", "x64"})
ARM64_ALIASES = frozenset({"aarch64", "arm64"})


@dataclass(frozen=True)
class RuntimePlatform:
    operating_system: str
    architecture: str

    @property
    def id(self) -> str:
        return f"{self.operating_system}-{self.architecture}"

    @property
    def windows(self) -> bool:
        return self.operating_system == "windows"

    @classmethod
    def current(cls) -> RuntimePlatform:
        return cls.from_names(platform.system(), platform.machine())

    @classmethod
    def from_names(cls, os_name: str, architecture: str) -> RuntimePlatform:
        system = os_name.lower()
        if "darwin" in system or "mac" in system:
            normalized_os = "macos"
        elif "windows" in system or system.startswith("win"):
            normalized_os = "windows"
        elif "linux" in system:
            normalized_os = "linux"
        else:
            raise ValueError(f"Unsupported operating system: {os_name}")
        normalized_arch = normalize_architecture(architecture, x86_name="x86_64")
        return cls(normalized_os, normalized_arch)


def normalize_architecture(value: str, *, x86_name: str) -> str:
    selected = value.lower().replace("-", "_")
    if selected in X86_64_ALIASES:
        return x86_name
    if selected in ARM64_ALIASES:
        return "arm64"
    raise ValueError(f"Unsupported architecture: {value}")


def portable_platform_id(platform_name: str | None = None, machine: str | None = None) -> str:
    """Return the archive/runtime platform ID used outside native-download properties."""
    selected_platform = sys.platform if platform_name is None else platform_name
    operating_system = OPERATING_SYSTEM_ALIASES.get(selected_platform)
    if operating_system is None:
        raise ValueError(f"Unsupported operating system: {selected_platform}")
    architecture = normalize_architecture(platform.machine() if machine is None else machine, x86_name="amd64")
    return f"{operating_system}-{architecture}"

# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Immutable comparison policy and deterministic endpoint selections."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from sbk_dashboard.contracts import BYTES_PER_MEBIBYTE, COMPARISON_TARGETS


@dataclass(frozen=True)
class ComparisonPolicy:
    """Server-owned bounds shared by API validation and Grafana descriptors."""

    max_targets: int = COMPARISON_TARGETS.default

    MIN_TARGETS: ClassVar[int] = 1
    MIN_SINGLE_TARGET_TIME_LANES: ClassVar[int] = 2
    MAX_TIME_LANES: ClassVar[int] = 8
    MAX_TIME_GROUPS: ClassVar[int] = 4
    MAX_ABSOLUTE_RANGE_DAYS: ClassVar[int] = 31
    MAX_CACHED_DASHBOARDS: ClassVar[int] = 128
    MAX_DESCRIPTOR_BYTES: ClassVar[int] = 2 * BYTES_PER_MEBIBYTE
    DESCRIPTOR_SCHEMA_VERSION: ClassVar[int] = 2
    DASHBOARD_PREFIX: ClassVar[str] = "sbk-comparison-"
    UID_DIGEST_HEX_LENGTH: ClassVar[int] = 16

    def __post_init__(self) -> None:
        if not COMPARISON_TARGETS.minimum <= self.max_targets <= COMPARISON_TARGETS.maximum:
            raise ValueError(
                "Maximum comparison targets must be between "
                f"{COMPARISON_TARGETS.minimum} and {COMPARISON_TARGETS.maximum}"
            )

    def selection(self, target_ids: Sequence[str]) -> ComparisonSelection:
        """Validate, normalize, and freeze one endpoint selection."""
        return ComparisonSelection.create(target_ids, self)

    def descriptor(self) -> dict[str, int]:
        """Return the complete browser-facing comparison policy contract."""
        return {
            "minTargets": self.MIN_TARGETS,
            "maxTargets": self.max_targets,
            "minSingleTargetTimeLanes": self.MIN_SINGLE_TARGET_TIME_LANES,
            "maxTimeLanes": self.MAX_TIME_LANES,
            "maxTimeGroups": self.MAX_TIME_GROUPS,
            "maxAbsoluteRangeDays": self.MAX_ABSOLUTE_RANGE_DAYS,
        }

    def matches_descriptor(self, value: object) -> bool:
        """Require every emitted policy field to match before reusing a cached descriptor."""
        return isinstance(value, dict) and value == self.descriptor()


@dataclass(frozen=True)
class ComparisonSelection:
    """A unique, order-independent endpoint set with a deterministic dashboard UID."""

    target_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.target_ids != tuple(sorted(set(self.target_ids))):
            raise ValueError("Comparison selection must contain sorted, unique endpoint IDs")

    @classmethod
    def create(cls, target_ids: Sequence[str], policy: ComparisonPolicy) -> ComparisonSelection:
        if len(target_ids) < policy.MIN_TARGETS:
            raise ValueError("Select at least one endpoint to compare")
        normalized = tuple(sorted(set(target_ids)))
        if len(normalized) != len(target_ids):
            raise ValueError("Comparison endpoints must be unique")
        if len(normalized) > policy.max_targets:
            raise ValueError(f"No more than {policy.max_targets} endpoints can be compared")
        return cls(normalized)

    @property
    def uid(self) -> str:
        digest = hashlib.sha256("\n".join(self.target_ids).encode()).hexdigest()[
            : ComparisonPolicy.UID_DIGEST_HEX_LENGTH
        ]
        return f"{ComparisonPolicy.DASHBOARD_PREFIX}{digest}"


def comparison_dashboard_uid(target_ids: Sequence[str]) -> str:
    """Return the stable UID for trusted IDs while preserving the historical helper contract."""
    normalized = tuple(sorted(set(target_ids)))
    return ComparisonSelection(normalized).uid

"""Stable endpoint identity and validation policy."""

from __future__ import annotations

from typing import TypeGuard

MIN_TCP_PORT = 1
MAX_TCP_PORT = 65_535
AUTO_PORT_FALLBACK_MINIMUM = 1024
ENDPOINT_ID_HEX_LENGTH = 16
MAX_ENDPOINT_NAME_CHARACTERS = 100
DEFAULT_METRICS_PATH = "/metrics"
DEFAULT_ENDPOINT_KIND = "SBK"
ALLOWED_ENDPOINT_KINDS = frozenset({"SBK", "SBM"})


def valid_port(value: object) -> TypeGuard[int]:
    """Return whether a value is an integer TCP port, excluding booleans."""
    return isinstance(value, int) and not isinstance(value, bool) and MIN_TCP_PORT <= value <= MAX_TCP_PORT

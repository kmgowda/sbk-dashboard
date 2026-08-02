"""Shared validation and normalization for network host values."""

from __future__ import annotations

import ipaddress
import re

DNS_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def normalize_host(value: str | None, name: str, *, allow_unspecified: bool) -> str:
    """Return a canonical IP literal or conservative DNS name without network I/O."""
    selected = value.strip() if value is not None else ""
    if not selected or len(selected) > 253 or any(character.isspace() for character in selected):
        raise ValueError(f"{name} is invalid")

    bracketed = selected.startswith("[") or selected.endswith("]")
    if bracketed:
        if not (selected.startswith("[") and selected.endswith("]")):
            raise ValueError(f"{name} is invalid")
        selected = selected[1:-1]
    if not selected or any(character in selected for character in "%/\\"):
        raise ValueError(f"{name} is invalid")

    try:
        address = ipaddress.ip_address(selected)
    except ValueError:
        if bracketed or ":" in selected:
            raise ValueError(f"{name} is invalid") from None
        normalized = selected.rstrip(".").lower()
        if not normalized or re.fullmatch(r"[0-9.]+", normalized):
            raise ValueError(f"{name} is invalid") from None
        labels = normalized.split(".")
        if any(DNS_LABEL_PATTERN.fullmatch(label) is None for label in labels):
            raise ValueError(f"{name} is invalid") from None
        return normalized

    if bracketed and address.version != 6:
        raise ValueError(f"{name} is invalid")
    if not allow_unspecified and address.is_unspecified:
        raise ValueError(f"{name} is invalid")
    return str(address)

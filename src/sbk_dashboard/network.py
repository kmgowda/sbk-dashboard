# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

"""Shared validation and normalization for network host values."""

from __future__ import annotations

import ipaddress
import re

MAX_DNS_NAME_CHARACTERS = 253
MAX_DNS_LABEL_INTERIOR_CHARACTERS = 61
DNS_LABEL_PATTERN = re.compile(
    rf"[A-Za-z0-9](?:[A-Za-z0-9-]{{0,{MAX_DNS_LABEL_INTERIOR_CHARACTERS}}}[A-Za-z0-9])?"
)


def normalize_host(value: str | None, name: str, *, allow_unspecified: bool) -> str:
    """Return a canonical IP literal or conservative DNS name without network I/O."""
    selected = value.strip() if value is not None else ""
    if (
        not selected
        or len(selected) > MAX_DNS_NAME_CHARACTERS
        or any(character.isspace() for character in selected)
    ):
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

    if bracketed and not isinstance(address, ipaddress.IPv6Address):
        raise ValueError(f"{name} is invalid")
    if not allow_unspecified and address.is_unspecified:
        raise ValueError(f"{name} is invalid")
    return str(address)

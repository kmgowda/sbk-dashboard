#!/usr/bin/env python3
"""Read one trusted native-artifact manifest value for container builds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FIELDS = frozenset({"url", "fileName", "sha256", "archiveDirectory", "executable", "archiveFormat"})
TOOLS = frozenset({"prometheus", "grafana"})


def manifest_value(manifest: dict[str, Any], tool: str, platform_id: str, field: str) -> str:
    if tool not in TOOLS or field not in FIELDS:
        raise ValueError("Unsupported native artifact selector")
    value = manifest["artifacts"][tool][platform_id][field]
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise ValueError("Native artifact manifest value must be a non-empty single line")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("tool", choices=sorted(TOOLS))
    parser.add_argument("platform")
    parser.add_argument("field", choices=sorted(FIELDS))
    arguments = parser.parse_args()
    manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    print(manifest_value(manifest, arguments.tool, arguments.platform, arguments.field))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Verify production and development Compose files have one identical runtime topology."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "sbk-dashboard"
ACQUISITION_KEYS = ("image", "pull_policy", "build")
RESOURCE_KEYS = ("cpus", "mem_limit", "pids_limit")


def resolved(*files: str) -> dict[str, Any]:
    command = ["docker", "compose"]
    for file_name in files:
        command.extend(("-f", file_name))
    command.extend(("config", "--format", "json"))
    completed = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    value: dict[str, Any] = json.loads(completed.stdout)
    return value


def runtime_definition(configuration: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(configuration))
    service = normalized["services"][SERVICE]
    for key in ACQUISITION_KEYS:
        service.pop(key, None)
    return normalized


def main() -> None:
    production = resolved("compose.yaml")
    development = resolved("compose.yaml", "compose.dev.yaml")
    resources = resolved("compose.yaml", "compose.resources.yaml")
    production_service = production["services"][SERVICE]
    development_service = development["services"][SERVICE]
    if "build" in production_service:
        raise AssertionError("Production Compose must consume a published image without a build section")
    if production_service.get("pull_policy") != "missing":
        raise AssertionError("Production Compose must pull only when the pinned image is missing")
    if "build" not in development_service or development_service.get("pull_policy") != "never":
        raise AssertionError("Development Compose must select an explicit local-only source build")
    if runtime_definition(production) != runtime_definition(development):
        raise AssertionError("Production and development Compose runtime definitions differ")
    resource_service = resources["services"][SERVICE]
    selected_resources = {key: resource_service.pop(key, None) for key in RESOURCE_KEYS}
    if resources != production:
        raise AssertionError("Resource overlay changes more than container resource limits")
    if selected_resources != {"cpus": 2, "mem_limit": "4294967296", "pids_limit": 512}:
        raise AssertionError(f"Unexpected default resource limits: {selected_resources}")
    print("Production/development runtime definitions and optional resource limits match")


if __name__ == "__main__":
    main()

"""Persistent endpoint registry."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from sbk_dashboard.files import atomic_json
from sbk_dashboard.models import BenchmarkTarget

HOST_PATTERN = re.compile(r"[A-Za-z0-9._:%-]+")


class TargetRegistry:
    """Thread-safe, atomically persisted unique host-and-port registrations."""

    def __init__(self, data_directory: Path) -> None:
        self._path = data_directory / "targets.json"
        self._lock = threading.RLock()
        data_directory.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            atomic_json(self._path, [])
        try:
            values = json.loads(self._path.read_text(encoding="utf-8"))
            loaded = [BenchmarkTarget.from_persisted(value) for value in values]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise OSError(f"Unable to load endpoint registry {self._path}: {error}") from error
        self._targets: dict[str, BenchmarkTarget] = {}
        for target in loaded:
            if target.id in self._targets:
                raise OSError(f"Duplicate target identifier in {self._path}: {target.id}")
            self._targets[target.id] = target

    def list(self) -> list[BenchmarkTarget]:
        with self._lock:
            return sorted(self._targets.values(), key=lambda item: item.name.casefold())

    def find(self, target_id: str) -> BenchmarkTarget | None:
        with self._lock:
            return self._targets.get(target_id)

    def register(self, name: str | None, host: str | None, port: int, metrics_path: str | None) -> BenchmarkTarget:
        normalized_host = self._validate_host(host)
        normalized_port = self._validate_port(port)
        normalized_path = self._validate_path(metrics_path)
        target_id = hashlib.sha256(f"{normalized_host}:{normalized_port}".encode()).hexdigest()[:16]
        normalized_name = name.strip() if name and name.strip() else f"{normalized_host}:{normalized_port}"
        if len(normalized_name) > 100:
            raise ValueError("Name must not exceed 100 characters")
        with self._lock:
            if target_id in self._targets:
                raise ValueError(f"The endpoint {normalized_host}:{normalized_port} is already registered")
            target = BenchmarkTarget(
                target_id, normalized_name, normalized_host, normalized_port, normalized_path, "SBK",
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            next_targets = dict(self._targets)
            next_targets[target_id] = target
            self._persist(next_targets)
            self._targets = next_targets
            return target

    def remove(self, target_id: str) -> bool:
        with self._lock:
            if target_id not in self._targets:
                return False
            next_targets = dict(self._targets)
            del next_targets[target_id]
            self._persist(next_targets)
            self._targets = next_targets
            return True

    def _persist(self, targets: dict[str, BenchmarkTarget]) -> None:
        atomic_json(self._path, [target.persisted() for target in targets.values()])

    @staticmethod
    def _validate_host(host: str | None) -> str:
        if host is None or not host.strip():
            raise ValueError("Host is required")
        value = host.strip()
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1]
        if len(value) > 253 or HOST_PATTERN.fullmatch(value) is None or ".." in value:
            raise ValueError("Host must be a DNS name, IPv4 address, or IPv6 address")
        return value.lower()

    @staticmethod
    def _validate_port(port: int) -> int:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return port

    @staticmethod
    def _validate_path(metrics_path: str | None) -> str:
        value = metrics_path.strip() if metrics_path and metrics_path.strip() else "/metrics"
        if not value.startswith("/") or any(character in value for character in "?# "):
            raise ValueError("Metrics path must be an absolute HTTP path")
        return value

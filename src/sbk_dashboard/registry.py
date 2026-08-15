"""Persistent endpoint registry."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sbk_dashboard.contracts import MAX_TARGETS
from sbk_dashboard.endpoint_policy import (
    ALLOWED_ENDPOINT_KINDS,
    DEFAULT_ENDPOINT_KIND,
    MAX_ENDPOINT_NAME_CHARACTERS,
    MAX_TCP_PORT,
    MIN_TCP_PORT,
    valid_port,
)
from sbk_dashboard.files import atomic_json
from sbk_dashboard.layout import DashboardDataLayout
from sbk_dashboard.models import BenchmarkTarget, endpoint_id, normalize_metrics_path
from sbk_dashboard.network import normalize_host


@dataclass(frozen=True)
class RegistrationResult:
    """The canonical target returned by an idempotent registration attempt."""

    target: BenchmarkTarget
    created: bool


class TargetRegistry:
    """Thread-safe, atomically persisted unique host-and-port registrations."""

    def __init__(self, data_directory: Path, max_targets: int = MAX_TARGETS.default) -> None:
        self._path = DashboardDataLayout(data_directory).targets
        self._max_targets = max_targets
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
        if len(self._targets) > self._max_targets:
            raise OSError(f"Endpoint registry exceeds configured maximum of {self._max_targets}")

    def list(self) -> list[BenchmarkTarget]:
        with self._lock:
            return sorted(self._targets.values(), key=lambda item: item.name.casefold())

    def find(self, target_id: str) -> BenchmarkTarget | None:
        with self._lock:
            return self._targets.get(target_id)

    def register(
        self,
        name: str | None,
        host: str | None,
        port: int,
        metrics_path: str | None,
        kind: str | None = None,
    ) -> BenchmarkTarget:
        return self.register_with_status(name, host, port, metrics_path, kind).target

    def register_with_status(
        self,
        name: str | None,
        host: str | None,
        port: int,
        metrics_path: str | None,
        kind: str | None = None,
    ) -> RegistrationResult:
        """Register a target, returning the existing target for an exact repeat."""
        if name is not None and not isinstance(name, str):
            raise ValueError("Name must be a string")
        normalized_host = self._validate_host(host)
        normalized_port = self._validate_port(port)
        normalized_path = self._validate_path(metrics_path)
        normalized_kind = (
            kind.strip().upper() if isinstance(kind, str) else DEFAULT_ENDPOINT_KIND if kind is None else ""
        )
        if normalized_kind not in ALLOWED_ENDPOINT_KINDS:
            raise ValueError("Kind must be SBK or SBM")
        target_id = endpoint_id(normalized_host, normalized_port)
        normalized_name = name.strip() if name and name.strip() else f"{normalized_host}:{normalized_port}"
        if len(normalized_name) > MAX_ENDPOINT_NAME_CHARACTERS:
            raise ValueError(f"Name must not exceed {MAX_ENDPOINT_NAME_CHARACTERS} characters")
        with self._lock:
            existing = self._targets.get(target_id)
            if existing is not None:
                if (
                    existing.name == normalized_name
                    and existing.metrics_path == normalized_path
                    and existing.kind == normalized_kind
                ):
                    return RegistrationResult(existing, False)
                raise ValueError(
                    f"The endpoint {normalized_host}:{normalized_port} is already registered "
                    "with different name, metrics path, or kind"
                )
            if len(self._targets) >= self._max_targets:
                raise ValueError(f"Endpoint limit of {self._max_targets} has been reached")
            target = BenchmarkTarget(
                target_id, normalized_name, normalized_host, normalized_port, normalized_path, normalized_kind,
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            next_targets = dict(self._targets)
            next_targets[target_id] = target
            self._persist(next_targets)
            self._targets = next_targets
            return RegistrationResult(target, True)

    def remove(self, target_id: str) -> bool:
        with self._lock:
            if target_id not in self._targets:
                return False
            next_targets = dict(self._targets)
            del next_targets[target_id]
            self._persist(next_targets)
            self._targets = next_targets
            return True

    def restore(self, target: BenchmarkTarget) -> None:
        """Atomically restore a trusted target during a failed reconciliation rollback."""
        with self._lock:
            if target.id in self._targets:
                raise ValueError(f"The endpoint {target.host}:{target.port} is already registered")
            if len(self._targets) >= self._max_targets:
                raise ValueError(f"Endpoint limit of {self._max_targets} has been reached")
            next_targets = dict(self._targets)
            next_targets[target.id] = target
            self._persist(next_targets)
            self._targets = next_targets

    def _persist(self, targets: dict[str, BenchmarkTarget]) -> None:
        atomic_json(self._path, [target.persisted() for target in targets.values()])

    @staticmethod
    def _validate_host(host: str | None) -> str:
        if not isinstance(host, str):
            raise ValueError("Host must be a DNS name, IPv4 address, or IPv6 address")
        try:
            return normalize_host(host, "Host", allow_unspecified=False)
        except ValueError:
            raise ValueError("Host must be a DNS name, IPv4 address, or IPv6 address") from None

    @staticmethod
    def _validate_port(port: int) -> int:
        if not valid_port(port):
            raise ValueError(f"Port must be between {MIN_TCP_PORT} and {MAX_TCP_PORT}")
        return port

    @staticmethod
    def _validate_path(metrics_path: str | None) -> str:
        return normalize_metrics_path(metrics_path)

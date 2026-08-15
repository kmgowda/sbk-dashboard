"""Persistent endpoint and health models."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass

from sbk_dashboard.endpoint_policy import (
    ALLOWED_ENDPOINT_KINDS,
    DEFAULT_ENDPOINT_KIND,
    DEFAULT_METRICS_PATH,
    ENDPOINT_ID_HEX_LENGTH,
    MAX_ENDPOINT_NAME_CHARACTERS,
    MAX_TCP_PORT,
    MIN_TCP_PORT,
    valid_port,
)
from sbk_dashboard.network import normalize_host


def endpoint_id(host: str, port: int) -> str:
    """Return the stable identity for one normalized endpoint."""
    return hashlib.sha256(f"{host}:{port}".encode()).hexdigest()[:ENDPOINT_ID_HEX_LENGTH]


def normalize_metrics_path(value: object) -> str:
    """Validate and normalize an endpoint metrics path."""
    if value is not None and not isinstance(value, str):
        raise ValueError("Metrics path must be an absolute HTTP path")
    selected = value.strip() if isinstance(value, str) and value.strip() else DEFAULT_METRICS_PATH
    if not selected.startswith("/") or any(character in selected for character in "?# "):
        raise ValueError("Metrics path must be an absolute HTTP path")
    return selected


@dataclass(frozen=True)
class BenchmarkTarget:
    """A remote Prometheus endpoint and its stable dashboard identity."""

    id: str
    name: str
    host: str
    port: int
    metrics_path: str
    kind: str
    created_at: str

    @property
    def prometheus_address(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def persisted(self) -> dict[str, object]:
        value = asdict(self)
        return {
            "id": value["id"],
            "name": value["name"],
            "host": value["host"],
            "port": value["port"],
            "metricsPath": value["metrics_path"],
            "kind": value["kind"],
            "createdAt": value["created_at"],
        }

    @classmethod
    def from_persisted(cls, value: dict[str, object]) -> BenchmarkTarget:
        raw_host = value["host"]
        if not isinstance(raw_host, str):
            raise TypeError("Persisted endpoint host must be a string")
        host = normalize_host(raw_host, "Persisted endpoint host", allow_unspecified=False)
        raw_port = value["port"]
        if isinstance(raw_port, bool) or not isinstance(raw_port, (str, int)):
            raise TypeError("Persisted endpoint port must be numeric")
        port = int(raw_port)
        if not valid_port(port):
            raise ValueError(f"Persisted endpoint port must be between {MIN_TCP_PORT} and {MAX_TCP_PORT}")
        raw_id = value["id"]
        if not isinstance(raw_id, str) or raw_id != endpoint_id(host, port):
            raise ValueError("Persisted endpoint identifier does not match its normalized host and port")
        raw_name = value["name"]
        if (
            not isinstance(raw_name, str)
            or not raw_name.strip()
            or len(raw_name) > MAX_ENDPOINT_NAME_CHARACTERS
        ):
            raise ValueError(
                f"Persisted endpoint name must contain between 1 and {MAX_ENDPOINT_NAME_CHARACTERS} characters"
            )
        raw_created_at = value["createdAt"]
        if not isinstance(raw_created_at, str) or not raw_created_at.strip():
            raise ValueError("Persisted endpoint creation time must be a non-empty string")
        raw_kind = value.get("kind", DEFAULT_ENDPOINT_KIND)
        if not isinstance(raw_kind, str) or raw_kind not in ALLOWED_ENDPOINT_KINDS:
            raise ValueError("Persisted endpoint kind must be SBK or SBM")
        return cls(
            raw_id,
            raw_name,
            host,
            port,
            normalize_metrics_path(value.get("metricsPath", DEFAULT_METRICS_PATH)),
            raw_kind,
            raw_created_at,
        )


@dataclass(frozen=True)
class TargetStatus:
    """Latest health reported by Prometheus for one target."""

    state: str = "pending"
    checked_at: str | None = None
    detail: str = "Waiting for the first health probe"

    def api(self) -> dict[str, object]:
        return {"state": self.state, "checkedAt": self.checked_at, "detail": self.detail}

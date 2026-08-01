"""Persistent endpoint and health models."""

from __future__ import annotations

from dataclasses import asdict, dataclass


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
        return cls(
            str(value["id"]), str(value["name"]), str(value["host"]), int(value["port"]),
            str(value.get("metricsPath", "/metrics")), str(value.get("kind", "SBK")), str(value["createdAt"]),
        )


@dataclass(frozen=True)
class TargetStatus:
    """Latest health reported by Prometheus for one target."""

    state: str = "pending"
    checked_at: str | None = None
    detail: str = "Waiting for the first health probe"

    def api(self) -> dict[str, object]:
        return {"state": self.state, "checkedAt": self.checked_at, "detail": self.detail}

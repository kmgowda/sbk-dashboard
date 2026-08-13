"""Threaded management HTTP server and endpoint API."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import socket
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import unquote, urlparse, urlsplit

from sbk_dashboard.models import BenchmarkTarget
from sbk_dashboard.monitoring import ManagedMonitoringStack
from sbk_dashboard.network import normalize_host
from sbk_dashboard.processes import LifecycleController, LifecycleState
from sbk_dashboard.registry import TargetRegistry

MAX_REQUEST_BYTES = 64 * 1024
MAX_COMPARISON_TARGETS = 8
MAX_TRACKED_CLIENTS = 10_000
LANDING_ACTIVITY_SECONDS = 120.0
GRAFANA_ACTIVITY_SECONDS = 300.0
CLIENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientActivitySummary:
    """Bounded recent-browser snapshot for the periodic operational status."""

    total: int
    landing: int
    grafana_opens: int


class RecentClientActivity:
    """Track opaque browser IDs in fixed-size, expiring LRU sets."""

    def __init__(self, capacity: int = MAX_TRACKED_CLIENTS) -> None:
        if capacity < 1:
            raise ValueError("Client activity capacity must be positive")
        self._capacity = capacity
        self._values: dict[str, OrderedDict[str, float]] = {
            "landing": OrderedDict(),
            "grafana": OrderedDict(),
        }
        self._lock = threading.Lock()

    def record(self, surface: str, client_id: str, now: float | None = None) -> None:
        if surface not in self._values:
            raise ValueError("Activity surface must be landing or grafana")
        if not CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ValueError("Client ID must contain 16 to 64 URL-safe characters")
        observed = time.monotonic() if now is None else now
        with self._lock:
            values = self._values[surface]
            values[client_id] = observed
            values.move_to_end(client_id)
            while len(values) > self._capacity:
                values.popitem(last=False)

    def summary(self, now: float | None = None) -> ClientActivitySummary:
        observed = time.monotonic() if now is None else now
        with self._lock:
            self._expire(self._values["landing"], observed - LANDING_ACTIVITY_SECONDS)
            self._expire(self._values["grafana"], observed - GRAFANA_ACTIVITY_SECONDS)
            landing = set(self._values["landing"])
            grafana = set(self._values["grafana"])
            return ClientActivitySummary(len(landing | grafana), len(landing), len(grafana))

    @staticmethod
    def _expire(values: OrderedDict[str, float], deadline: float) -> None:
        while values:
            _, timestamp = next(iter(values.items()))
            if timestamp > deadline:
                return
            values.popitem(last=False)


class DashboardHttpServer:
    def __init__(self, port: int, registry: TargetRegistry, monitoring: ManagedMonitoringStack) -> None:
        self.registry = registry
        self.monitoring = monitoring
        self.lifecycle = LifecycleController()
        self._close_lock = threading.Lock()
        self._mutation_lock = threading.Lock()
        self._client_activity = RecentClientActivity()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_POST(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_DELETE(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_PUT(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_PATCH(self) -> None:  # noqa: N802
                owner._handle(self)

            def log_message(self, message_format: str, *args: object) -> None:
                return

        config = monitoring.dashboard
        self._default_target_host = config.default_target_host
        self._server = BoundedThreadPoolHttpServer(
            (config.bind_address, port),
            Handler,
            workers=config.http_workers,
            queue_capacity=config.http_queue_capacity,
            request_timeout=config.request_timeout_seconds,
        )
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.lifecycle.transition(LifecycleState.STARTING)
        self._thread = threading.Thread(target=self._server.serve_forever, name="sbk-http-server", daemon=True)
        self._thread.start()
        self.lifecycle.transition(LifecycleState.RUNNING)

    def close(self) -> None:
        if self._server.called_from_worker():
            raise RuntimeError("DashboardHttpServer.close() must not be called from an HTTP worker thread")
        with self._close_lock:
            state = self.lifecycle.state
            if state == LifecycleState.STOPPED:
                return
            if state == LifecycleState.NEW:
                self._server.server_close()
                self._server.close_pool()
                self.lifecycle.transition(LifecycleState.STOPPED)
                return
            self.lifecycle.transition(LifecycleState.STOPPING)
            self._server.shutdown()
            self._server.server_close()
            if self._thread and self._thread is not threading.current_thread():
                self._thread.join(timeout=3)
            self._server.close_pool()
            self.lifecycle.transition(LifecycleState.STOPPED)

    def client_activity(self) -> ClientActivitySummary:
        return self._client_activity.summary()

    def _handle(self, request: BaseHTTPRequestHandler) -> None:
        path = urlparse(request.path).path
        try:
            if path == "/api/health":
                self._require(request, "GET")
                healthy = self.monitoring.healthy()
                self._json(request, 200 if healthy else 503,
                           {"status": "ok" if healthy else "degraded", "authentication": False,
                            "targets": len(self.registry.list())})
            elif path.startswith("/api/activity/"):
                self._activity(request, path[len("/api/activity/"):])
            elif path == "/api/targets":
                self._targets(request)
            elif path == "/api/comparison-dashboard":
                self._comparison_dashboard(request)
            elif path.startswith("/api/targets/"):
                self._target(request, path[len("/api/targets/"):])
            else:
                self._asset(request, path)
        except MethodNotAllowed as error:
            self._json(request, 405, {"error": str(error)}, {"Allow": error.expected})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self._json(request, 400, {"error": str(error) or "Request body is not valid JSON"})
        except OSError as error:
            LOGGER.error("Request failed: %s", error)
            self._json(request, 500, {"error": "Unable to update dashboard state"})
        except Exception as error:  # defensive HTTP boundary
            LOGGER.exception("Unexpected request failure: %s", error)
            self._json(request, 500, {"error": "Unexpected server error"})

    def _activity(self, request: BaseHTTPRequestHandler, surface: str) -> None:
        self._require(request, "POST")
        body = self._read_json(request)
        client_id = body.get("clientId")
        if not isinstance(client_id, str):
            raise ValueError("Client ID must be a string")
        self._client_activity.record(surface, client_id)
        request.send_response(HTTPStatus.NO_CONTENT)
        request.send_header("Cache-Control", "no-store")
        request.end_headers()

    def _targets(self, request: BaseHTTPRequestHandler) -> None:
        if request.command == "GET":
            self._json(request, 200, [self._view(request, target) for target in self.registry.list()])
            return
        self._require(request, "POST")
        body = self._read_json(request)
        port = body.get("port")
        if isinstance(port, bool) or not isinstance(port, int):
            raise ValueError("Port must be between 1 and 65535")
        with self._mutation_lock:
            target = self.registry.register(
                body.get("name"), body.get("host"), port, body.get("metricsPath"), body.get("kind")
            )
            try:
                self.monitoring.reconcile(self.registry.list())
            except Exception:
                try:
                    if not self.registry.remove(target.id):
                        raise OSError("Registered target disappeared before rollback")
                except Exception as rollback_error:
                    raise OSError("Unable to roll back failed target registration") from rollback_error
                self._best_effort_reconcile("registration rollback")
                raise
        self._json(request, 201, self._view(request, target))

    def _comparison_dashboard(self, request: BaseHTTPRequestHandler) -> None:
        self._require(request, "POST")
        body = self._read_json(request)
        values = body.get("targetIds")
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("Target IDs must be an array of strings")
        if len(values) < 2:
            raise ValueError("Select at least two endpoints to compare")
        if len(values) > MAX_COMPARISON_TARGETS:
            raise ValueError(f"No more than {MAX_COMPARISON_TARGETS} endpoints can be compared")
        target_ids = list(dict.fromkeys(values))
        if len(target_ids) != len(values):
            raise ValueError("Comparison endpoints must be unique")
        with self._mutation_lock:
            if any(self.registry.find(target_id) is None for target_id in target_ids):
                raise ValueError("Every comparison endpoint must be registered")
            dashboard_url = self.monitoring.comparison_dashboard_url(
                target_ids, self._request_hostname(request)
            )
        self._json(request, 200, {"dashboardUrl": dashboard_url})

    def _target(self, request: BaseHTTPRequestHandler, encoded: str) -> None:
        identifier, separator, action = encoded.partition("/")
        target_id = unquote(identifier)
        target = self.registry.find(target_id)
        if "/" in target_id or target is None:
            self._json(request, 404, {"error": "Target not found"})
            return
        if separator and action == "dashboard":
            self._require(request, "GET")
            self._json(request, 200, {"dashboardUrl": self._dashboard_url(request, target_id)})
            return
        if separator:
            self._json(request, 404, {"error": "Not found"})
            return
        self._require(request, "DELETE")
        with self._mutation_lock:
            target = self.registry.find(target_id)
            if target is None or not self.registry.remove(target_id):
                self._json(request, 404, {"error": "Target not found"})
                return
            try:
                self.monitoring.reconcile(self.registry.list())
            except Exception:
                try:
                    self.registry.restore(target)
                except Exception as rollback_error:
                    raise OSError("Unable to roll back failed target deletion") from rollback_error
                self._best_effort_reconcile("deletion rollback")
                raise
        request.send_response(HTTPStatus.NO_CONTENT)
        request.end_headers()

    def _best_effort_reconcile(self, operation: str) -> None:
        try:
            self.monitoring.reconcile(self.registry.list())
        except Exception as error:
            LOGGER.warning("Monitoring %s could not be reconciled: %s", operation, error)

    def _asset(self, request: BaseHTTPRequestHandler, path: str) -> None:
        self._require(request, "GET")
        assets = {"/": "index.html", "/index.html": "index.html", "/app.css": "app.css", "/app.js": "app.js"}
        name = assets.get(path)
        if name is None:
            self._json(request, 404, {"error": "Not found"})
            return
        resource_root = files("sbk_dashboard").joinpath("resources/web")
        resource = resource_root.joinpath(name)
        try:
            body = resource.read_bytes()
            if name == "index.html":
                fingerprint = hashlib.sha256(
                    resource_root.joinpath("app.css").read_bytes()
                    + resource_root.joinpath("app.js").read_bytes()
                ).hexdigest()[:12]
                body = body.replace(b"__ASSET_VERSION__", fingerprint.encode("ascii"))
                body = body.replace(
                    b"__DEFAULT_TARGET_HOST__",
                    html.escape(self._default_target_host, quote=True).encode("utf-8"),
                )
            elif name == "app.js":
                body = body.replace(
                    b"__MAX_COMPARISON_TARGETS__",
                    str(MAX_COMPARISON_TARGETS).encode("ascii"),
                )
        except OSError:
            self._json(request, 500, {"error": "Missing application asset"})
            return
        content_type = (
            "text/html" if name.endswith(".html") else "text/css" if name.endswith(".css") else "text/javascript"
        )
        request.send_response(200)
        request.send_header("Content-Type", f"{content_type}; charset=utf-8")
        # Asset URLs are stable across releases. Require revalidation so a newly
        # deployed HTML document can never run with an older cached script/style.
        request.send_header("Cache-Control", "no-cache")
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)

    def _view(self, request: BaseHTTPRequestHandler, target: BenchmarkTarget) -> dict[str, Any]:
        return {
            "id": target.id, "name": target.name, "host": target.host, "port": target.port,
            "metricsPath": target.metrics_path, "kind": target.kind, "createdAt": target.created_at,
            "status": self.monitoring.status(target.id).api(),
            "dashboardUrl": self._dashboard_url(request, target.id),
        }

    def _dashboard_url(self, request: BaseHTTPRequestHandler, target_id: str) -> str:
        return self.monitoring.dashboard_url(target_id, self._request_hostname(request))

    @staticmethod
    def _request_hostname(request: BaseHTTPRequestHandler) -> str | None:
        """Extract a hostname without reflecting a malformed Host header into links."""
        value = request.headers.get("Host", "").strip()
        if not value or any(character in value for character in "\r\n/\\"):
            return None
        try:
            parsed = urlsplit(f"//{value}")
            hostname = parsed.hostname
            _ = parsed.port  # Validate an optional port before using the hostname.
        except ValueError:
            return None
        if not hostname or any(character.isspace() for character in hostname):
            return None
        try:
            return normalize_host(hostname, "Host header", allow_unspecified=True)
        except ValueError:
            return None

    @staticmethod
    def _read_json(request: BaseHTTPRequestHandler) -> dict[str, Any]:
        try:
            length = int(request.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length < 0:
            raise ValueError("Invalid Content-Length")
        if length > MAX_REQUEST_BYTES:
            raise ValueError("Request body exceeds 64 KiB")
        value = json.loads(request.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    @staticmethod
    def _require(request: BaseHTTPRequestHandler, method: str) -> None:
        if request.command != method:
            raise MethodNotAllowed(method)

    @staticmethod
    def _json(request: BaseHTTPRequestHandler, status: int, value: object,
              extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        request.send_response(status)
        request.send_header("Content-Type", "application/json; charset=utf-8")
        request.send_header("Cache-Control", "no-store")
        request.send_header("Content-Length", str(len(body)))
        for name, header_value in (extra_headers or {}).items():
            request.send_header(name, header_value)
        request.end_headers()
        request.wfile.write(body)


class MethodNotAllowed(RuntimeError):
    def __init__(self, expected: str) -> None:
        super().__init__(f"Use {expected} for this endpoint")
        self.expected = expected


class BoundedThreadPoolHttpServer(HTTPServer):
    """Fixed-worker Active Object with bounded admission and explicit backpressure."""

    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        workers: int,
        queue_capacity: int,
        request_timeout: int,
    ) -> None:
        self.request_queue_size = min(max(workers + queue_capacity, 5), 256)
        if ":" in server_address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(server_address, handler)
        self._request_timeout = request_timeout
        self._capacity = threading.BoundedSemaphore(workers + queue_capacity)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sbk-http-worker")

    @staticmethod
    def called_from_worker() -> bool:
        return threading.current_thread().name.startswith("sbk-http-worker")

    def server_bind(self) -> None:
        if self.address_family == socket.AF_INET6 and hasattr(socket, "IPV6_V6ONLY"):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int] | tuple[str, int, int, int],
    ) -> None:
        if not isinstance(request, socket.socket):
            raise TypeError("SBK Dashboard requires a stream socket")
        if not self._capacity.acquire(blocking=False):
            self._reject_overload(request)
            return
        try:
            future = self._executor.submit(self._process_request, request, client_address)
        except BaseException:
            self._capacity.release()
            self.shutdown_request(request)
            raise
        future.add_done_callback(lambda completed: self._request_completed(completed.cancelled(), request))

    def close_pool(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _process_request(
        self, request: socket.socket, client_address: tuple[str, int] | tuple[str, int, int, int]
    ) -> None:
        request.settimeout(self._request_timeout)
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def _request_completed(self, cancelled: bool, request: socket.socket) -> None:
        if cancelled:
            self.shutdown_request(request)
        self._capacity.release()

    def _reject_overload(self, request: socket.socket) -> None:
        body = b'{"error":"Server is at request capacity"}'
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Connection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        try:
            request.settimeout(1)
            request.sendall(response)
        except OSError:
            pass
        finally:
            self.shutdown_request(request)

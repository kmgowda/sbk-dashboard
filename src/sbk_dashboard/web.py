"""Threaded management HTTP server and endpoint API."""

from __future__ import annotations

import json
import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import unquote, urlparse, urlsplit

from sbk_dashboard.models import BenchmarkTarget
from sbk_dashboard.monitoring import ManagedMonitoringStack
from sbk_dashboard.processes import LifecycleController, LifecycleState
from sbk_dashboard.registry import TargetRegistry

MAX_REQUEST_BYTES = 64 * 1024
LOGGER = logging.getLogger(__name__)


class DashboardHttpServer:
    def __init__(self, port: int, registry: TargetRegistry, monitoring: ManagedMonitoringStack) -> None:
        self.registry = registry
        self.monitoring = monitoring
        self.lifecycle = LifecycleController()
        self._close_lock = threading.Lock()
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

    def _handle(self, request: BaseHTTPRequestHandler) -> None:
        path = urlparse(request.path).path
        try:
            if path == "/api/health":
                self._require(request, "GET")
                healthy = self.monitoring.healthy()
                self._json(request, 200 if healthy else 503,
                           {"status": "ok" if healthy else "degraded", "authentication": False,
                            "targets": len(self.registry.list())})
            elif path == "/api/targets":
                self._targets(request)
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

    def _targets(self, request: BaseHTTPRequestHandler) -> None:
        if request.command == "GET":
            self._json(request, 200, [self._view(request, target) for target in self.registry.list()])
            return
        self._require(request, "POST")
        body = self._read_json(request)
        port = body.get("port")
        if not isinstance(port, int):
            raise ValueError("Port must be between 1 and 65535")
        target = self.registry.register(body.get("name"), body.get("host"), port, body.get("metricsPath"))
        try:
            self.monitoring.reconcile(self.registry.list())
        except OSError:
            self.registry.remove(target.id)
            self.monitoring.reconcile(self.registry.list())
            raise
        self._json(request, 201, self._view(request, target))

    def _target(self, request: BaseHTTPRequestHandler, encoded: str) -> None:
        identifier, separator, action = encoded.partition("/")
        target_id = unquote(identifier)
        if "/" in target_id or self.registry.find(target_id) is None:
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
        if not self.registry.remove(target_id):
            self._json(request, 404, {"error": "Target not found"})
            return
        self.monitoring.reconcile(self.registry.list())
        request.send_response(HTTPStatus.NO_CONTENT)
        request.end_headers()

    def _asset(self, request: BaseHTTPRequestHandler, path: str) -> None:
        self._require(request, "GET")
        assets = {"/": "index.html", "/index.html": "index.html", "/app.css": "app.css", "/app.js": "app.js"}
        name = assets.get(path)
        if name is None:
            self._json(request, 404, {"error": "Not found"})
            return
        resource = files("sbk_dashboard").joinpath(f"resources/web/{name}")
        try:
            body = resource.read_bytes()
        except OSError:
            self._json(request, 500, {"error": "Missing application asset"})
            return
        content_type = (
            "text/html" if name.endswith(".html") else "text/css" if name.endswith(".css") else "text/javascript"
        )
        request.send_response(200)
        request.send_header("Content-Type", f"{content_type}; charset=utf-8")
        request.send_header("Cache-Control", "no-cache" if name.endswith(".html") else "public, max-age=3600")
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)

    def _view(self, request: BaseHTTPRequestHandler, target: BenchmarkTarget) -> dict[str, Any]:
        return {
            "id": target.id, "name": target.name, "host": target.host, "port": target.port,
            "metricsPath": target.metrics_path, "createdAt": target.created_at,
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
        return hostname

    @staticmethod
    def _read_json(request: BaseHTTPRequestHandler) -> dict[str, Any]:
        try:
            length = int(request.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
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

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
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

    def _process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
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

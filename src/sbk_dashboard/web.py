"""Threaded management HTTP server and endpoint API."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import unquote, urlparse

from sbk_dashboard.models import BenchmarkTarget
from sbk_dashboard.monitoring import ManagedMonitoringStack
from sbk_dashboard.registry import TargetRegistry

MAX_REQUEST_BYTES = 64 * 1024


class DashboardHttpServer:
    def __init__(self, port: int, registry: TargetRegistry, monitoring: ManagedMonitoringStack) -> None:
        self.registry = registry
        self.monitoring = monitoring
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

        self._server = ThreadingHTTPServer(("", port), Handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, name="sbk-http-server", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

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
            print(f"Request failed: {error}")
            self._json(request, 500, {"error": "Unable to update dashboard state"})
        except Exception as error:  # defensive HTTP boundary
            print(f"Request failed: {error}")
            self._json(request, 500, {"error": "Unexpected server error"})

    def _targets(self, request: BaseHTTPRequestHandler) -> None:
        if request.command == "GET":
            self._json(request, 200, [self._view(target) for target in self.registry.list()])
            return
        self._require(request, "POST")
        body = self._read_json(request)
        target = self.registry.register(body.get("name"), body.get("host"), body.get("port"), body.get("metricsPath"))
        try:
            self.monitoring.reconcile(self.registry.list())
        except OSError:
            self.registry.remove(target.id)
            self.monitoring.reconcile(self.registry.list())
            raise
        self._json(request, 201, self._view(target))

    def _target(self, request: BaseHTTPRequestHandler, encoded: str) -> None:
        identifier, separator, action = encoded.partition("/")
        target_id = unquote(identifier)
        if "/" in target_id or self.registry.find(target_id) is None:
            self._json(request, 404, {"error": "Target not found"})
            return
        if separator and action == "dashboard":
            self._require(request, "GET")
            self._json(request, 200, {"dashboardUrl": self.monitoring.dashboard_url(target_id)})
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

    def _view(self, target: BenchmarkTarget) -> dict[str, Any]:
        return {
            "id": target.id, "name": target.name, "host": target.host, "port": target.port,
            "metricsPath": target.metrics_path, "createdAt": target.created_at,
            "status": self.monitoring.status(target.id).api(),
            "dashboardUrl": self.monitoring.dashboard_url(target.id),
        }

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

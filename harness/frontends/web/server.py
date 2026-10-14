"""
Modular Web Server and Event Stream Frontend for Harness-Zero.
Translates HTTP/SSE requests into Commands API calls and EventBus streams (§11).
Implements Dual-Origin Isolation (API on Port 8000, Sandbox on Port 8001)
with strict CSP, host header guards, and replayable SSE event stream.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from harness.kernel.bus import EventBus, Subscriber
from harness.kernel.commands import Commands
from harness.sdk.errors import HarnessError
from harness.sdk.events import Event

logger = logging.getLogger("harness.frontends.web.server")


class HarnessWebServer:
    """
    Modular HTTP and SSE server bridging the browser UI to the Kernel.
    """

    def __init__(
        self,
        commands: Commands,
        bus: EventBus,
        host: str = "127.0.0.1",
        port: int = 8000,
        sandbox_port: int = 8001,
        auth_token: str | None = None,
        allowed_hosts: list[str] | None = None,
        static_dir: Path | None = None,
        workspace: Path | None = None,
    ) -> None:
        self.commands = commands
        self.bus = bus
        self.host = host
        self.port = port
        self.sandbox_port = sandbox_port
        self.auth_token = auth_token or os.environ.get("HARNESS_AUTH_TOKEN") or secrets.token_urlsafe(32)
        self.allowed_hosts = set(allowed_hosts or [])
        self.workspace = workspace or commands.workspace
        self.static_dir = static_dir or (Path(__file__).parent / "static")

        # 500-event ring buffer for Last-Event-ID replay
        self._replay_buffer: deque[Event] = deque(maxlen=500)
        self._replay_sub: Subscriber | None = None
        self._replay_task: asyncio.Task | None = None

        # Setup primary API and sandbox web apps
        self.app = web.Application(
            middlewares=[self._host_guard, self._auth_guard],
            client_max_size=25 * 1024 * 1024,
        )
        self.sandbox_app = web.Application(
            middlewares=[self._host_guard, self._sandbox_csp_middleware],
        )

        self._setup_api_routes()
        self._setup_sandbox_routes()

        self.api_runner: web.AppRunner | None = None
        self.sandbox_runner: web.AppRunner | None = None
        self.api_site: web.TCPSite | None = None
        self.sandbox_site: web.TCPSite | None = None

    # --------------------------------------------------------------------------
    # Middlewares
    # --------------------------------------------------------------------------

    @web.middleware
    async def _host_guard(self, request: web.Request, handler: Callable) -> web.Response:
        """Protects against DNS rebinding attacks by validating the Host header."""
        host_header = request.headers.get("Host", "")
        if not host_header:
            return web.json_response({"error": "Missing Host header"}, status=421)

        if host_header.startswith("["):
            hostname = host_header.split("]")[0] + "]"
        else:
            hostname = host_header.split(":")[0].strip().lower()

        allowed = {
            "127.0.0.1",
            "localhost",
            "::1",
            "[::1]",
            self.host.lower(),
        } | {h.lower() for h in self.allowed_hosts}

        if hostname not in allowed:
            return web.json_response(
                {"error": f"Misdirected Request: host '{host_header}' not allowed"},
                status=421,
            )

        return await handler(request)

    @web.middleware
    async def _auth_guard(self, request: web.Request, handler: Callable) -> web.Response:
        """
        Enforces Bearer / X-Harness-Token authentication and CSRF origin checks.
        Public paths (/health, /, /ui, /favicon.ico, static assets) bypass auth.
        """
        path = request.path
        method = request.method

        # Public endpoints (no token required)
        is_public = (
            (method == "GET" and path in ("/", "/ui", "/health", "/favicon.ico"))
            or path.startswith("/static/")
            or path.startswith("/assets/")
            or path.startswith("/vendor/")
            or any(path.endswith(ext) for ext in (".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff2"))
        )

        if not is_public:
            # 1. Bearer / X-Harness-Token check
            token = request.headers.get("X-Harness-Token")
            if not token:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:].strip()
            if not token and "token" in request.query:
                token = request.query.get("token")

            if not token or not hmac.compare_digest(token, self.auth_token):
                return web.json_response({"error": "unauthorized"}, status=401)

            # 2. Mutating requests CSRF / Origin / Content-Type enforcement
            if method not in ("GET", "HEAD", "OPTIONS"):
                ct = request.headers.get("Content-Type", "")
                is_empty_body = (request.content_length == 0 or not request.can_read_body) and not ct
                if not is_empty_body:
                    if not (ct.startswith("application/json") or ct.startswith("multipart/form-data")):
                        return web.json_response(
                            {"error": "Unsupported Media Type: expected application/json"},
                            status=415,
                        )

                origin = request.headers.get("Origin")
                if origin:
                    allowed_origins = {
                        f"http://127.0.0.1:{self.port}",
                        f"http://localhost:{self.port}",
                    }
                    if self.port == 0:
                        allowed_origins.add("http://127.0.0.1")
                        allowed_origins.add("http://localhost")
                    origin_allowed = (origin in allowed_origins) or (
                        self.port == 0 and (origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:"))
                    )
                    if not origin_allowed:
                        return web.json_response({"error": "Cross-Origin forbidden"}, status=403)

                fetch_site = request.headers.get("Sec-Fetch-Site")
                if fetch_site == "cross-site":
                    return web.json_response({"error": "Cross-Site requests forbidden"}, status=403)

        response = await handler(request)

        # Inject Security Headers for API responses (HTML/Assets)
        if isinstance(response, web.StreamResponse):
            sb_origin_127 = f"http://127.0.0.1:{self.sandbox_port}" if self.sandbox_port else "http://127.0.0.1"
            sb_origin_local = f"http://localhost:{self.sandbox_port}" if self.sandbox_port else "http://localhost"
            csp = (
                "default-src 'self'; "
                "img-src 'self' data: blob:; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; "
                f"frame-src {sb_origin_127} {sb_origin_local}; "
                "frame-ancestors 'none'; "
                "connect-src 'self' ws:;"
            )
            response.headers.setdefault("Content-Security-Policy", csp)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")

        return response

    @web.middleware
    async def _sandbox_csp_middleware(self, request: web.Request, handler: Callable) -> web.Response:
        """Enforces strictly isolated Content-Security-Policy on the Sandbox origin."""
        response = await handler(request)
        if isinstance(response, web.StreamResponse):
            api_origin_127 = f"http://127.0.0.1:{self.port}" if self.port else "http://127.0.0.1"
            api_origin_local = f"http://localhost:{self.port}" if self.port else "http://localhost"
            sandbox_csp = (
                "default-src 'none'; "
                "style-src 'unsafe-inline'; "
                "script-src 'none'; "
                f"frame-ancestors {api_origin_127} {api_origin_local}; "
                "connect-src 'none';"
            )
            response.headers["Content-Security-Policy"] = sandbox_csp
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # --------------------------------------------------------------------------
    # API App Routes
    # --------------------------------------------------------------------------

    def _setup_api_routes(self) -> None:
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/ui", self._handle_index)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_get("/events", self._handle_events)
        self.app.router.add_post("/commands/{name}", self._handle_command)
        self.app.router.add_post("/rpc", self._handle_rpc)

        # Static assets
        if self.static_dir.exists():
            self.app.router.add_static("/static/", path=str(self.static_dir), name="static")

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serves the studio index.html or minimal bootstrap container."""
        index_file = self.static_dir / "index.html"
        if index_file.exists():
            return web.FileResponse(index_file)
        # Minimal skeleton if static dir is not yet populated
        content = (
            "<!DOCTYPE html>"
            "<html lang='en'><head><meta charset='UTF-8'><title>Harness Studio</title></head>"
            "<body><div id='app'>Harness Studio Loading...</div></body></html>"
        )
        return web.Response(text=content, content_type="text/html")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Reports server status, active session, spend, and sandbox origin."""
        session = self.commands.session_manager.active_session
        spend_info = await self.commands.economics(session_id=session.session_id if session else None)
        return web.json_response({
            "status": "ok",
            "version": "2.0.0",
            "active_session": session.info.to_dict() if session else None,
            "sandbox_origin": f"http://{self.host}:{self.sandbox_port}",
            "spend": spend_info,
        })

    async def _handle_command(self, request: web.Request) -> web.Response:
        """Direct JSON RPC dispatcher to Commands API (§11)."""
        name = request.match_info["name"]
        args: dict[str, Any] = {}
        if request.can_read_body and request.content_length:
            try:
                args = await request.json()
            except Exception as e:
                return web.json_response(
                    {"error": f"Invalid JSON payload: {e}", "status": "error"},
                    status=400,
                )

        try:
            result = await self.commands.dispatch(name, args)
            return web.json_response({"result": result, "status": "ok"})
        except HarnessError as e:
            return web.json_response({"error": str(e), "status": "error"}, status=400)
        except Exception as e:
            logger.exception(f"Unexpected error executing command '{name}': {e}")
            return web.json_response({"error": str(e), "status": "error"}, status=500)

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        """Generic JSON-RPC bridge accepting {command: str, args: dict}."""
        try:
            body = await request.json()
        except Exception as e:
            return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

        cmd = body.get("command") or body.get("method")
        if not cmd:
            return web.json_response({"error": "Missing 'command' or 'method'"}, status=400)
        args = body.get("args") or body.get("params") or {}

        try:
            result = await self.commands.dispatch(cmd, args)
            return web.json_response({"result": result, "status": "ok"})
        except HarnessError as e:
            return web.json_response({"error": str(e), "status": "error"}, status=400)
        except Exception as e:
            logger.exception(f"Unexpected error executing command '{cmd}': {e}")
            return web.json_response({"error": str(e), "status": "error"}, status=500)

    async def _handle_events(self, request: web.Request) -> web.StreamResponse:
        """
        Long-lived SSE stream of the EventBus with heartbeat and Last-Event-ID replay.
        Heartbeat: 15s. Replay buffer: up to 500 events.
        """
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        # Check Last-Event-ID for replay
        last_event_id = request.headers.get("Last-Event-ID") or request.query.get("last_event_id")
        if last_event_id:
            # Replay buffered events strictly following the client's last seen ID
            events_to_replay: list[Event] = []
            found = False
            for evt in list(self._replay_buffer):
                if found:
                    events_to_replay.append(evt)
                elif evt.id == last_event_id:
                    found = True

            for evt in events_to_replay:
                msg = f"id: {evt.id}\nevent: {evt.type}\ndata: {json.dumps(evt.to_dict())}\n\n"
                await response.write(msg.encode("utf-8"))

        subscriber = self.bus.subscribe(interactive=True)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(subscriber.get(), timeout=15.0)
                    msg = f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event.to_dict())}\n\n"
                    await response.write(msg.encode("utf-8"))
                except TimeoutError:
                    # 15s Heartbeat ping to keep connection alive through proxies
                    await response.write(b": heartbeat\n\n")
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            self.bus.unsubscribe(subscriber)

        return response

    # --------------------------------------------------------------------------
    # Sandbox App Routes
    # --------------------------------------------------------------------------

    def _setup_sandbox_routes(self) -> None:
        self.sandbox_app.router.add_get("/", self._handle_sandbox_index)
        self.sandbox_app.router.add_get("/artifacts/{filename}", self._handle_sandbox_artifact)
        self.sandbox_app.router.add_get("/preview", self._handle_sandbox_preview)

    async def _handle_sandbox_index(self, request: web.Request) -> web.Response:
        """Default response for the sandbox preview origin."""
        content = (
            "<!DOCTYPE html>"
            "<html><head><meta charset='utf-8'><title>Harness Sandbox</title></head>"
            "<body><p style='color:#888;font-family:sans-serif;'>Harness Sandboxed Preview</p></body></html>"
        )
        return web.Response(text=content, content_type="text/html")

    async def _handle_sandbox_artifact(self, request: web.Request) -> web.Response:
        """Serves workspace artifacts with path traversal prevention."""
        filename = request.match_info["filename"]
        artifacts_dir = (self.workspace / ".harness" / "artifacts").resolve()
        target = (artifacts_dir / filename).resolve()

        # Security check: must reside inside artifacts_dir
        if not str(target).startswith(str(artifacts_dir)):
            return web.json_response({"error": "Access denied"}, status=403)

        if not target.exists() or not target.is_file():
            return web.json_response({"error": "Artifact not found"}, status=404)

        suffix = target.suffix.lower()
        content_type = "text/plain"
        if suffix in (".html", ".htm"):
            content_type = "text/html"
        elif suffix in (".md", ".markdown"):
            content_type = "text/markdown"
        elif suffix == ".json":
            content_type = "application/json"
        elif suffix == ".svg":
            content_type = "image/svg+xml"

        content = target.read_bytes()
        return web.Response(body=content, content_type=content_type)

    async def _handle_sandbox_preview(self, request: web.Request) -> web.Response:
        """Renders arbitrary HTML content passed via session preview storage."""
        html = request.query.get("html", "<p>No content</p>")
        return web.Response(text=html, content_type="text/html")

    # --------------------------------------------------------------------------
    # Replay Buffer Background Consumer
    # --------------------------------------------------------------------------

    async def _run_replay_consumer(self) -> None:
        """Background task buffering the last 500 events from the EventBus."""
        self._replay_sub = self.bus.subscribe(interactive=False)
        try:
            while True:
                evt = await self._replay_sub.get()
                self._replay_buffer.append(evt)
        except asyncio.CancelledError:
            pass
        finally:
            if self._replay_sub:
                self.bus.unsubscribe(self._replay_sub)

    # --------------------------------------------------------------------------
    # Lifecycle & daemon.json Management
    # --------------------------------------------------------------------------

    def write_daemon_json(self) -> Path:
        """Writes .harness/daemon.json with 0600 file permissions."""
        harness_dir = self.workspace / ".harness"
        harness_dir.mkdir(parents=True, exist_ok=True)
        daemon_file = harness_dir / "daemon.json"

        data = {
            "port": self.port,
            "sandbox_port": self.sandbox_port,
            "token": self.auth_token,
            "pid": os.getpid(),
        }
        content = json.dumps(data, indent=2).encode("utf-8")
        fd = os.open(daemon_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "wb") as f:
            f.write(content)
        return daemon_file

    def cleanup_daemon_json(self) -> None:
        """Safely cleans up the daemon.json file on shutdown."""
        daemon_file = self.workspace / ".harness" / "daemon.json"
        try:
            if daemon_file.exists():
                daemon_file.unlink()
        except Exception:
            pass

    async def start(self) -> tuple[web.AppRunner, web.AppRunner]:
        """Starts both API and Sandbox HTTP runners."""
        # 1. Start Replay Buffer Consumer
        self._replay_task = asyncio.create_task(self._run_replay_consumer())

        # 2. Setup and start primary API site
        self.api_runner = web.AppRunner(self.app)
        await self.api_runner.setup()
        self.api_site = web.TCPSite(self.api_runner, host=self.host, port=self.port)
        await self.api_site.start()

        # Read actual bound port if port was 0
        if self.api_site._server and self.api_site._server.sockets:
            self.port = self.api_site._server.sockets[0].getsockname()[1]

        # 3. Setup and start sandbox site
        self.sandbox_runner = web.AppRunner(self.sandbox_app)
        await self.sandbox_runner.setup()
        self.sandbox_site = web.TCPSite(self.sandbox_runner, host=self.host, port=self.sandbox_port)
        await self.sandbox_site.start()

        if self.sandbox_site._server and self.sandbox_site._server.sockets:
            self.sandbox_port = self.sandbox_site._server.sockets[0].getsockname()[1]

        # 4. Write daemon.json
        self.write_daemon_json()

        logger.info(
            f"Harness Web Server listening on API http://{self.host}:{self.port} "
            f"and Sandbox http://{self.host}:{self.sandbox_port}"
        )
        return self.api_runner, self.sandbox_runner

    async def stop(self) -> None:
        """Shuts down both HTTP runners, cancels background tasks, and cleans up."""
        if self._replay_task:
            self._replay_task.cancel()
            try:
                await self._replay_task
            except asyncio.CancelledError:
                pass

        if self.api_runner:
            await self.api_runner.cleanup()
        if self.sandbox_runner:
            await self.sandbox_runner.cleanup()

        self.cleanup_daemon_json()

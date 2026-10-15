"""
Modular Web Server and Event Stream Frontend for Harness-Zero.
Translates HTTP/SSE requests into Commands API calls and EventBus streams (§11).
Implements Dual-Origin Isolation (API on Port 8000, Sandbox on Port 8001)
with strict CSP, host header guards, and replayable SSE event stream.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hmac
import json
import logging
import os
import secrets
import subprocess
import time
import uuid
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


def _json_dumps(obj: Any) -> str:
    def _default(o: Any) -> Any:
        if hasattr(o, "to_dict") and callable(o.to_dict):
            return o.to_dict()
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, (set, frozenset)):
            return list(o)
        if hasattr(o, "value"):
            return o.value
        raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

    return json.dumps(obj, default=_default)


def _json_response(data: Any, status: int = 200, **kwargs) -> web.Response:
    return web.json_response(data, status=status, dumps=_json_dumps, **kwargs)


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

        # Short-lived single-use auth tickets for SSE/EventSource (§2.2)
        self._auth_tickets: dict[str, float] = {}
        self._purge_workspace_secrets()

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
            authenticated = False

            # 1. Bearer / X-Harness-Token check
            token = request.headers.get("X-Harness-Token")
            if not token:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:].strip()

            if token and hmac.compare_digest(token, self.auth_token):
                authenticated = True

            # 2. HttpOnly Cookie check
            if not authenticated:
                cookie_token = request.cookies.get("harness_token")
                if cookie_token and hmac.compare_digest(cookie_token, self.auth_token):
                    authenticated = True

            # 3. Single-use ticket check (e.g. for EventSource / SSE)
            if not authenticated and "ticket" in request.query:
                ticket = request.query.get("ticket")
                if ticket:
                    now = time.time()
                    expiry = self._auth_tickets.pop(ticket, None)
                    if expiry and now <= expiry:
                        authenticated = True

            # Master token in query string is strictly disallowed to prevent token leaks in URLs/history
            if not authenticated:
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

        if request.path == "/events":
            return await handler(request)

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
                "img-src data: blob:; "
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
        self.app.router.add_post("/auth/ticket", self._handle_auth_ticket)
        self.app.router.add_get("/events", self._handle_events)
        self.app.router.add_post("/commands/{name}", self._handle_command)
        self.app.router.add_post("/rpc", self._handle_rpc)

        # Rich Studio API Endpoints
        self.app.router.add_post("/turn/stream", self._handle_turn_stream)
        self.app.router.add_post("/turn/cancel", self._handle_turn_cancel)
        self.app.router.add_get("/files", self._handle_files)
        self.app.router.add_get("/file/content", self._handle_file_content)
        self.app.router.add_post("/file/save", self._handle_file_save)
        self.app.router.add_get("/diffs", self._handle_diffs)
        self.app.router.add_get("/artifacts", self._handle_artifacts_list)
        self.app.router.add_get("/artifacts/{filename}", self._handle_get_artifact)
        self.app.router.add_get("/sessions", self._handle_sessions_list)
        self.app.router.add_get("/sessions/{session_id}", self._handle_session_get)
        self.app.router.add_post("/sessions/new", self._handle_session_new)
        self.app.router.add_post("/sessions/switch", self._handle_session_switch)
        self.app.router.add_get("/economics", self._handle_economics)
        self.app.router.add_get("/skills", self._handle_skills)
        self.app.router.add_get("/skills/{name}", self._handle_skill_playbook)
        self.app.router.add_get("/plugins", self._handle_plugins)
        self.app.router.add_get("/settings", self._handle_settings_get)
        self.app.router.add_post("/settings", self._handle_settings_post)
        self.app.router.add_post("/search/code", self._handle_search_code)
        self.app.router.add_post("/media/upload", self._handle_media_upload)
        self.app.router.add_get("/media/{filename}", self._handle_media_serve)
        self.app.router.add_get("/tasks", self._handle_tasks)
        self.app.router.add_get("/spawn-tree", self._handle_spawn_tree)

        # Static assets
        assets_dir = self.static_dir / "assets"
        if assets_dir.exists():
            self.app.router.add_static("/assets", str(assets_dir), name="assets")
        if self.static_dir.exists():
            self.app.router.add_static("/static/", path=str(self.static_dir), name="static")

    async def _handle_auth_ticket(self, request: web.Request) -> web.Response:
        """Issues a 60-second single-use ticket for EventSource / SSE connection."""
        now = time.time()
        # Clean expired tickets
        self._auth_tickets = {t: exp for t, exp in self._auth_tickets.items() if exp > now}
        ticket = secrets.token_urlsafe(32)
        self._auth_tickets[ticket] = now + 60.0
        return _json_response({"ticket": ticket, "expires_in": 60})

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serves the rich studio index.html with tokens injected and sets HttpOnly cookie."""
        index_file = self.static_dir / "index.html"
        if index_file.exists():
            html = index_file.read_text(encoding="utf-8")
            html = html.replace("__HARNESS_TOKEN__", self.auth_token)
            html = html.replace("__SANDBOX_PORT__", str(self.sandbox_port))
            response = web.Response(text=html, content_type="text/html")
        else:
            # Minimal skeleton if static dir is not yet populated
            content = (
                "<!DOCTYPE html>"
                f"<html lang='en'><head><meta charset='UTF-8'><meta name='harness-token' content='{self.auth_token}'><title>Harness Studio</title></head>"
                "<body><div id='app'>Harness Studio Loading...</div></body></html>"
            )
            response = web.Response(text=content, content_type="text/html")

        response.set_cookie(
            "harness_token",
            self.auth_token,
            httponly=True,
            samesite="Strict",
            path="/",
        )
        return response

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
            return _json_response({"result": result, "status": "ok"})
        except HarnessError as e:
            return _json_response({"error": str(e), "status": "error"}, status=400)
        except Exception as e:
            logger.exception(f"Unexpected error executing command '{name}': {e}")
            return _json_response({"error": str(e), "status": "error"}, status=500)

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        """Generic JSON-RPC bridge accepting {command: str, args: dict}."""
        try:
            body = await request.json()
        except Exception as e:
            return _json_response({"error": f"Invalid JSON: {e}"}, status=400)

        cmd = body.get("command") or body.get("method")
        if not cmd:
            return _json_response({"error": "Missing 'command' or 'method'"}, status=400)
        args = body.get("args") or body.get("params") or {}

        try:
            result = await self.commands.dispatch(cmd, args)
            return _json_response({"result": result, "status": "ok"})
        except HarnessError as e:
            return _json_response({"error": str(e), "status": "error"}, status=400)
        except Exception as e:
            logger.exception(f"Unexpected error executing command '{cmd}': {e}")
            return _json_response({"error": str(e), "status": "error"}, status=500)

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
                if request.transport is None or request.transport.is_closing():
                    break
                try:
                    event = await asyncio.wait_for(subscriber.get(), timeout=15.0)
                    if request.transport is None or request.transport.is_closing():
                        break
                    msg = f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event.to_dict())}\n\n"
                    await response.write(msg.encode("utf-8"))
                except TimeoutError:
                    if request.transport is None or request.transport.is_closing():
                        break
                    # 15s Heartbeat ping to keep connection alive through proxies
                    await response.write(b": heartbeat\n\n")
        except (asyncio.CancelledError, ConnectionResetError, ConnectionError, Exception):
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
    # Rich Studio Handlers
    # --------------------------------------------------------------------------

    async def _handle_turn_stream(self, request: web.Request) -> web.StreamResponse:
        """SSE streaming endpoint for live agent character output, thinking, and tools."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        prompt = body.get("prompt", "")
        model = body.get("model")
        mode = body.get("mode", "autonomous")

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        async def send_sse(event_type: str, data: dict):
            payload = json.dumps({"type": event_type, **data})
            await response.write(f"data: {payload}\n\n".encode())

        subscriber = self.bus.subscribe(interactive=True)

        async def relay_bus_events():
            try:
                while True:
                    evt = await subscriber.get()
                    if evt.type in ("model.text.delta", "model.delta"):
                        delta_text = evt.data.get("text") or evt.data.get("delta", "")
                        await send_sse("text_delta", {"delta": delta_text})
                    elif evt.type == "model.thinking.delta":
                        delta_text = evt.data.get("text") or evt.data.get("delta", "")
                        await send_sse("thinking_delta", {"delta": delta_text})
                    elif evt.type == "tool.call":
                        await send_sse("tool_call", {"call": evt.data})
                    elif evt.type == "tool.result":
                        await send_sse("tool_result", {"result": evt.data})
                    elif evt.type == "approval.required":
                        await send_sse("approval_required", evt.data)
                    elif evt.type in ("turn.completed", "turn.error", "turn.cancelled"):
                        break
            except asyncio.CancelledError:
                pass
            finally:
                self.bus.unsubscribe(subscriber)

        relay_task = asyncio.create_task(relay_bus_events())

        try:
            await send_sse("turn_start", {"prompt": prompt, "mode": mode, "model": model})
            session = self.commands.session_manager.active_session
            if not session:
                session = await self.commands.session_manager.create_session()
            session_id = session.session_id

            options: dict[str, Any] = {}
            if model:
                options["model"] = model
            if body.get("provider"):
                options["provider"] = body["provider"]
            if mode:
                options["mode"] = mode

            result = await self.commands.submit_turn(
                session_id=session_id,
                prompt=prompt,
                options=options,
            )

            await send_sse("turn_complete", {
                "result": {
                    "output": result.output if hasattr(result, "output") else str(result),
                    "thinking": getattr(result, "thinking", ""),
                    "tool_calls": getattr(result, "tool_calls", 0),
                    "error": getattr(result, "error", None),
                }
            })
        except Exception as e:
            await send_sse("turn_error", {"error": str(e)})
        finally:
            relay_task.cancel()
            try:
                await relay_task
            except asyncio.CancelledError:
                pass
            await response.write_eof()

        return response

    async def _handle_turn_cancel(self, request: web.Request) -> web.Response:
        """Cancels the active turn."""
        try:
            await self.commands.cancel_turn()
            return web.json_response({"status": "cancelled"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_files(self, request: web.Request) -> web.Response:
        """Returns workspace files tree for the Explorer view."""
        files = []
        for p in self.workspace.rglob("*"):
            if any(part.startswith(".") for part in p.parts):
                continue
            if "__pycache__" in p.parts or "node_modules" in p.parts or "dist" in p.parts:
                continue
            if p.is_file():
                try:
                    rel = p.relative_to(self.workspace)
                    files.append({
                        "path": str(rel),
                        "name": p.name,
                        "size": p.stat().st_size,
                        "ext": p.suffix,
                    })
                except (OSError, ValueError):
                    continue
        return web.json_response({"files": files[:150]})

    async def _handle_file_content(self, request: web.Request) -> web.Response:
        """Reads file content safely from workspace."""
        rel_path = request.query.get("path", "").strip()
        if not rel_path:
            return web.json_response({"error": "Missing path parameter"}, status=400)

        target = (self.workspace / rel_path).resolve()
        ws_resolved = self.workspace.resolve()
        try:
            target.relative_to(ws_resolved)
        except ValueError:
            return web.json_response({"error": "Access denied: Path outside workspace"}, status=403)

        if not target.exists() or not target.is_file():
            return web.json_response({"error": f"File '{rel_path}' not found"}, status=404)

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            return web.json_response({
                "path": rel_path,
                "name": target.name,
                "ext": target.suffix,
                "lines_count": len(lines),
                "size": target.stat().st_size,
                "content": content,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_file_save(self, request: web.Request) -> web.Response:
        """Saves file content to workspace atomically."""
        try:
            body = await request.json()
            rel_path = body.get("path", "").strip()
            content = body.get("content", "")
            if not rel_path:
                return web.json_response({"error": "Missing path parameter"}, status=400)

            target = (self.workspace / rel_path).resolve()
            ws_resolved = self.workspace.resolve()
            try:
                target.relative_to(ws_resolved)
            except ValueError:
                return web.json_response({"error": "Access denied: Path outside workspace"}, status=403)

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return web.json_response({"status": "saved", "path": rel_path, "size": len(content)})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_diffs(self, request: web.Request) -> web.Response:
        """Returns workspace git diff for the Diff Studio view."""
        try:
            res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=5,
            )
            raw_diff = res.stdout
            if not raw_diff:
                res_unstaged = subprocess.run(
                    ["git", "diff"],
                    cwd=str(self.workspace),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                raw_diff = res_unstaged.stdout

            lines = raw_diff.splitlines()
            files_changed = []
            for line in lines:
                if line.startswith("diff --git a/"):
                    parts = line.split()
                    if len(parts) >= 4:
                        files_changed.append(parts[2][2:])
            return web.json_response({
                "diff": raw_diff,
                "modified_files": list(dict.fromkeys(files_changed)),
                "has_diff": bool(raw_diff.strip()),
            })
        except Exception as e:
            return web.json_response({"diff": "", "modified_files": [], "has_diff": False, "error": str(e)})

    async def _handle_artifacts_list(self, request: web.Request) -> web.Response:
        """Returns artifacts manifest."""
        manifest: dict[str, Any] = {}
        artifacts_dir = self.workspace / "artifacts"
        if not artifacts_dir.exists():
            artifacts_dir = self.workspace / ".harness" / "artifacts"
        if artifacts_dir.exists():
            for f in artifacts_dir.glob("*"):
                if f.is_file() and not f.name.startswith("."):
                    manifest[f.name] = {
                        "name": f.name,
                        "size": f.stat().st_size,
                        "path": str(f),
                        "ext": f.suffix,
                    }
        return web.json_response({"manifest": manifest})

    async def _handle_get_artifact(self, request: web.Request) -> web.Response:
        filename = request.match_info.get("filename", "")
        artifacts_dir = self.workspace / "artifacts"
        if not (artifacts_dir / filename).exists():
            artifacts_dir = self.workspace / ".harness" / "artifacts"
        target = artifacts_dir / filename
        if not target.exists() or not target.is_file():
            return web.Response(text="Artifact not found", status=404)
        return web.FileResponse(target)

    async def _handle_sessions_list(self, request: web.Request) -> web.Response:
        sessions = self.commands.session_manager.list_sessions()
        active = self.commands.session_manager.active_session
        formatted_sessions = [
            s if isinstance(s, dict) else s.info.to_dict() for s in sessions
        ]
        return _json_response({
            "active_session_id": active.session_id if active else None,
            "sessions": formatted_sessions,
        })

    async def _handle_session_get(self, request: web.Request) -> web.Response:
        session_id = request.match_info.get("session_id", "")
        session = self.commands.session_manager.get_session(session_id)
        if not session:
            return _json_response({"error": "Session not found"}, status=404)
        history = await session.get_history()
        return _json_response({
            "session_id": session_id,
            "is_current": session == self.commands.session_manager.active_session,
            "history": history,
        })

    async def _handle_session_new(self, request: web.Request) -> web.Response:
        session = await self.commands.session_manager.create_session()
        return _json_response({"status": "created", "session_id": session.session_id})

    async def _handle_session_switch(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            sid = body.get("session_id")
            if not sid or "/" in sid or "\\" in sid or ".." in sid:
                return _json_response({"error": "Invalid session_id"}, status=400)
            session = self.commands.session_manager.get_session(sid)
            if not session:
                return _json_response({"error": "Session not found"}, status=404)
            await self.commands.session_manager.switch_session(sid)
            return _json_response({"status": "switched", "session_id": sid})
        except Exception as e:
            return _json_response({"error": str(e)}, status=400)

    async def _handle_economics(self, request: web.Request) -> web.Response:
        session = self.commands.session_manager.active_session
        spend_info = await self.commands.economics(session_id=session.session_id if session else None)
        return web.json_response(spend_info)

    async def _handle_skills(self, request: web.Request) -> web.Response:
        skills = []
        skills_dir = Path(__file__).resolve().parents[3] / "builtin" / "skills"
        if skills_dir.exists():
            for s in skills_dir.iterdir():
                if s.is_dir():
                    skill_md = s / "SKILL.md"
                    desc = ""
                    if skill_md.exists():
                        lines = skill_md.read_text(encoding="utf-8", errors="replace").splitlines()
                        for line in lines:
                            if line.startswith("description:"):
                                desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                                break
                    skills.append({
                        "name": s.name,
                        "tier": "builtin",
                        "description": desc or f"Skill: {s.name}",
                        "path": str(s),
                    })
        return web.json_response({"skills": skills})

    async def _handle_skill_playbook(self, request: web.Request) -> web.Response:
        name = request.match_info.get("name", "")
        skills_dir = Path(__file__).resolve().parents[3] / "builtin" / "skills"
        skill_file = skills_dir / name / "SKILL.md"
        if not skill_file.exists():
            return web.Response(text="Skill not found", status=404)
        return web.Response(text=skill_file.read_text(encoding="utf-8"), content_type="text/plain")

    async def _handle_plugins(self, request: web.Request) -> web.Response:
        try:
            plugins = await self.commands.plugins_list()
        except Exception as e:
            logger.warning(f"Failed to fetch plugins list: {e}")
            plugins = []

        tools = []
        try:
            for spec in self.commands.registry.get_all_tool_specs():
                tools.append({
                    "name": spec.name,
                    "description": spec.description,
                    "risk_class": spec.risk_class.value if hasattr(spec.risk_class, "value") else str(spec.risk_class),
                    "capabilities": [c.value if hasattr(c, "value") else str(c) for c in spec.capabilities],
                })
        except Exception as e:
            logger.warning(f"Failed to fetch tools: {e}")

        providers = []
        try:
            for prov in self.commands.registry.get_all_providers():
                providers.append({
                    "name": prov.name,
                    "models": list(getattr(prov, "supported_models", [])),
                })
        except Exception as e:
            logger.warning(f"Failed to fetch providers: {e}")

        return web.json_response({
            "plugins": plugins,
            "servers": {
                "mcp_server": {"name": "Harness MCP Server", "status": "active", "port": self.port, "protocol": "jsonrpc-2.0"},
                "acp_bridge": {"name": "Agent Client Protocol (ACP)", "status": "active", "protocol": "acp-1.0"},
            },
            "tools": tools,
            "providers": providers,
        })

    def _purge_workspace_secrets(self) -> None:
        """Purges any plaintext api_keys from workspace settings and enforces 0600 mode (§4.3 #1)."""
        sf = self.workspace / ".harness" / "settings.json"
        if not sf.exists():
            return
        try:
            try:
                os.chmod(sf, 0o600)
            except Exception:
                pass
            data = json.loads(sf.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "api_keys" in data:
                api_keys = data.pop("api_keys", {})
                if isinstance(api_keys, dict):
                    for prov, key in api_keys.items():
                        if key and isinstance(key, str) and not key.startswith("●"):
                            self.commands.secrets.set(f"{prov.lower()}_api_key", key)
                            self.commands.secrets.set(f"{prov.upper()}_API_KEY", key)
                sf.write_text(json.dumps(data, indent=2), encoding="utf-8")
                try:
                    os.chmod(sf, 0o600)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Error purging workspace secrets from {sf}: {e}")

    def _settings_file(self) -> Path:
        harness_dir = self.workspace / ".harness"
        harness_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(harness_dir, 0o700)
        except Exception:
            pass
        return harness_dir / "settings.json"

    def _get_cowork_settings(self) -> dict[str, Any]:
        sf = self._settings_file()
        stored = {}
        if sf.exists():
            try:
                stored = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                pass

        if isinstance(stored, dict):
            stored.pop("api_keys", None)
            stored.pop("configured_providers", None)
            stored.pop("has_api_keys", None)

        providers_status = {}
        for prov in ("openai", "anthropic", "gemini", "openrouter", "deepseek", "tirith"):
            has = (
                self.commands.secrets.has(f"{prov}_api_key")
                or self.commands.secrets.has(f"{prov.upper()}_API_KEY")
            )
            providers_status[prov] = has

        cur_perm = self.commands.settings.get("kernel.security.permission_mode", "ask")
        cur_spend = self.commands.settings.get("kernel.security.spend_cap_usd", 5.0)
        cur_destr = self.commands.settings.get("kernel.security.destructive_blocking", True)
        cur_prov = self.commands.settings.get("kernel.provider", "mock")
        cur_model = self.commands.settings.get("kernel.model", "mock-default")

        defaults = {
            "general": {
                "theme": "dark",
                "locale": "en-US",
                "launch_at_login": False,
                "keep_in_background": True,
                "telemetry_enabled": False,
            },
            "account": {
                "account_id": "00000000-0000-0000-0000-000000000000",
                "email": "user@local.device",
                "organization_id": "00000000-0000-0000-0000-000000000000",
                "is_signed_in": True,
                "keyring_status": "unlocked",
            },
            "cowork": {
                "remote_control_enabled": True,
                "cowork_user_files_path": str(self.workspace),
                "trusted_folders": [str(self.workspace)],
                "bypass_permissions_gate": False,
                "ble_maker_enabled": False,
            },
            "capabilities": {
                "web_search_enabled": True,
                "browser_tools_enabled": True,
                "preferred_browser": "built_in",
                "allowed_sites": [
                    f"127.0.0.1:{self.port}",
                    f"127.0.0.1:{self.sandbox_port}",
                ],
                "network_sandbox_mode": "restricted",
                "shell_execution_mode": "workspace_only",
                "file_system_guard": "jail_active",
            },
            "memory": {
                "vector_memory_enabled": True,
                "sqlite_fts5_enabled": True,
                "session_retention_days": 30,
                "auto_summarize_threshold": 80,
                "cross_session_context": True,
            },
            "reflect": {
                "architect_evaluator_enabled": True,
                "self_correction_passes": 2,
                "lint_feedback_loop": True,
                "git_commit_verification": True,
                "failover_circuit_breaker": True,
            },
            "time_focus": {
                "pomodoro_timer_minutes": 25,
                "break_timer_minutes": 5,
                "session_spend_cap_usd": 5.0,
                "max_tool_iterations_per_turn": 30,
                "turn_timeout_seconds": 180,
            },
            "claude_code": {
                "auto_compact_context": True,
                "prefix_invariance_caching": True,
                "prompt_cache_ttl_minutes": 5,
                "repomap_page_rank": True,
                "token_budget_strategy": "adaptive",
            },
            "security": {
                "default_permission_mode": cur_perm,
                "destructive_command_blocking": cur_destr,
                "tirith_ssrf_shield": True,
                "session_spend_cap_usd": cur_spend,
            },
            "models": {
                "default_provider": cur_prov,
                "default_model": cur_model,
                "temperature": 0.2,
                "max_tokens": 4096,
            },
            "configured_providers": dict(providers_status),
            "has_api_keys": dict(providers_status),
        }

        for k, v in stored.items():
            if k in ("configured_providers", "has_api_keys", "api_keys"):
                continue
            if isinstance(v, dict) and k in defaults and isinstance(defaults[k], dict):
                defaults[k].update(v)
            else:
                defaults[k] = v

        defaults["configured_providers"] = dict(providers_status)
        defaults["has_api_keys"] = dict(providers_status)
        return defaults

    async def _handle_settings_get(self, request: web.Request) -> web.Response:
        settings = self._get_cowork_settings()
        settings.pop("api_keys", None)
        return _json_response(settings)

    async def _handle_settings_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            cur = self._get_cowork_settings()
            for section in ("general", "account", "privacy", "billing", "capabilities",
                            "memory", "reflect", "time_focus", "claude_code", "system",
                            "extensions", "developer", "skills", "connectors", "plugins",
                            "models", "security", "cowork"):
                if section in body and isinstance(body[section], dict):
                    if section not in cur:
                        cur[section] = {}
                    cur[section].update(body[section])

            keys = body.get("api_keys")
            if isinstance(keys, dict):
                for prov, key in keys.items():
                    if key and isinstance(key, str) and not key.startswith("●"):
                        self.commands.secrets.set(f"{prov.lower()}_api_key", key)
                        self.commands.secrets.set(f"{prov.upper()}_API_KEY", key)

            # Wire security toggles into kernel SettingsStore (§4.3 #3)
            sec = body.get("security")
            if isinstance(sec, dict):
                perm_mode = sec.get("default_permission_mode")
                if perm_mode in ("allow", "ask", "plan", "bypass"):
                    self.commands.settings.set("kernel.security.permission_mode", perm_mode)
                spend_cap = sec.get("session_spend_cap_usd")
                if spend_cap is not None:
                    try:
                        self.commands.settings.set("kernel.security.spend_cap_usd", float(spend_cap))
                    except (ValueError, TypeError):
                        pass
                destr = sec.get("destructive_command_blocking")
                if destr is not None:
                    self.commands.settings.set("kernel.security.destructive_blocking", bool(destr))

            provider = body.get("default_provider") or (body.get("models", {}).get("default_provider") if isinstance(body.get("models"), dict) else None)
            model = body.get("default_model") or (body.get("models", {}).get("default_model") if isinstance(body.get("models"), dict) else None)
            if provider:
                self.commands.settings.set("kernel.provider", provider)
            if model:
                self.commands.settings.set("kernel.model", model)

            # Never persist api_keys in workspace settings.json; enforce mode 0600 (§4.3 #1)
            to_save = dict(cur)
            to_save.pop("api_keys", None)
            to_save.pop("configured_providers", None)
            to_save.pop("has_api_keys", None)
            sf = self._settings_file()
            sf.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
            try:
                os.chmod(sf, 0o600)
            except Exception:
                pass

            # Update configured_providers in response
            providers_status = {}
            for prov in ("openai", "anthropic", "gemini", "openrouter", "deepseek", "tirith"):
                has = (
                    self.commands.secrets.has(f"{prov}_api_key")
                    or self.commands.secrets.has(f"{prov.upper()}_API_KEY")
                )
                providers_status[prov] = has
            cur["configured_providers"] = providers_status
            cur["has_api_keys"] = providers_status

            return _json_response({
                "status": "saved",
                "settings": cur,
                "active_provider": provider,
                "configured_providers": providers_status,
                "has_api_keys": providers_status,
            })
        except Exception as e:
            return _json_response({"error": str(e)}, status=400)

    async def _handle_search_code(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            query = body.get("query", "").strip()
            if not query:
                return web.json_response({"results": [], "query": ""})
            res = subprocess.run(
                ["grep", "-rnI", "--exclude-dir=.git", "--exclude-dir=__pycache__", "--exclude-dir=.venv", "-m", "50", query, "."],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=5,
            )
            results = []
            for line in res.stdout.splitlines()[:50]:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    results.append({
                        "file": parts[0].removeprefix("./"),
                        "line": int(parts[1]) if parts[1].isdigit() else 1,
                        "text": parts[2].strip(),
                    })
            return web.json_response({"results": results, "query": query})
        except Exception as e:
            return web.json_response({"results": [], "error": str(e)})

    async def _handle_media_upload(self, request: web.Request) -> web.Response:
        """Uploads media (images, files) into active session media store."""
        media_dir = self.workspace / ".harness" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        uploaded = []
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            while True:
                field = await reader.next()
                if field is None:
                    break
                if field.filename:
                    safe_name = f"{uuid.uuid4().hex[:6]}_{Path(field.filename).name.replace(' ', '_')}"
                    dest = media_dir / safe_name
                    size = 0
                    with open(dest, "wb") as f:
                        while True:
                            chunk = await field.read_chunk()
                            if not chunk:
                                break
                            size += len(chunk)
                            f.write(chunk)
                    uploaded.append({
                        "filename": safe_name,
                        "url": f"/media/{safe_name}",
                        "mime_type": field.headers.get("Content-Type", "application/octet-stream"),
                        "size": size,
                    })
        return web.json_response({"uploaded": uploaded, "status": "ok"})

    async def _handle_media_serve(self, request: web.Request) -> web.Response:
        filename = request.match_info.get("filename", "")
        target = self.workspace / ".harness" / "media" / filename
        if not target.exists() or not target.is_file():
            return web.Response(text="Media not found", status=404)
        return web.FileResponse(target)

    async def _handle_spawn_tree(self, request: web.Request) -> web.Response:
        return web.json_response({"tree": []})

    async def _handle_tasks(self, request: web.Request) -> web.Response:
        return web.json_response({"tasks": []})

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

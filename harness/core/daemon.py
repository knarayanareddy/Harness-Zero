"""
Daemon IPC Server for Harness-Zero.
Implements Dual-Port Origin Separation (Port 8000 API vs Port 8001 Preview Sandbox)
synthesized from Claude Cowork (Operon) for browser SOP defense, plus JSON-RPC 2.0.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from aiohttp import web

from harness.core.coordinator import AgentCoordinator

logger = logging.getLogger("harness.daemon")


class HarnessDaemon:
    def __init__(
        self,
        coordinator: AgentCoordinator,
        host: str = "127.0.0.1",
        port: int = 8000,
        sandbox_port: int = 8001,
        socket_path: Optional[Path] = None,
    ):
        self.coordinator = coordinator
        self.host = host
        self.port = port
        self.sandbox_port = sandbox_port
        self.socket_path = socket_path

        self._login_tokens: Dict[str, float] = {}

        # Dual-Port Isolation Applications
        self.app = web.Application()
        self.sandbox_app = web.Application()

        self._setup_api_routes()
        self._setup_sandbox_routes()

    def generate_login_url(self) -> str:
        """
        Generates a cryptographically random, single-use 3-minute login link.
        Synthesized from Claude Cowork's 'claude-science url' remote SSH access pattern.
        """
        import secrets
        import time

        token = secrets.token_urlsafe(32)
        # Valid for 180 seconds (3 minutes)
        self._login_tokens[token] = time.time() + 180.0
        return f"http://{self.host}:{self.port}/login?token={token}"

    def _setup_api_routes(self) -> None:
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/ui", self._handle_index)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_post("/rpc", self._handle_rpc)
        self.app.router.add_get("/skills", self._handle_skills)
        self.app.router.add_get("/tasks", self._handle_tasks)
        self.app.router.add_get("/files", self._handle_files)
        self.app.router.add_get("/artifacts", self._handle_artifacts_list)
        self.app.router.add_get("/artifacts/{filename}", self._handle_get_artifact)
        self.app.router.add_get("/spawn-tree", self._handle_spawn_tree)
        self.app.router.add_get("/economics", self._handle_economics)
        self.app.router.add_post("/search/code", self._handle_search_code)
        self.app.router.add_get("/settings", self._handle_settings_get)
        self.app.router.add_post("/settings", self._handle_settings_post)
        self.app.router.add_get("/events", self._handle_events)
        self.app.router.add_get("/login", self._handle_login)

        # Serve static assets (Tailwind, Jetbox, Prism)
        assets_dir = Path(__file__).resolve().parent.parent / "web" / "assets"
        if assets_dir.exists():
            self.app.router.add_static("/assets", str(assets_dir))

    async def _handle_files(self, request: web.Request) -> web.Response:
        """Returns workspace files tree for the Explorer view."""
        files = []
        for p in self.coordinator.workspace.rglob("*"):
            if any(part.startswith(".") for part in p.parts):
                continue
            if "__pycache__" in p.parts or "node_modules" in p.parts or "dist" in p.parts:
                continue
            if p.is_file():
                rel = p.relative_to(self.coordinator.workspace)
                files.append({
                    "path": str(rel),
                    "name": p.name,
                    "size": p.stat().st_size,
                    "ext": p.suffix,
                })
        return web.json_response({"files": files[:100]})

    async def _handle_index(self, request: web.Request) -> web.Response:
        web_file = Path(__file__).resolve().parent.parent / "web" / "index.html"
        if web_file.exists():
            return web.Response(text=web_file.read_text(encoding="utf-8"), content_type="text/html")
        return web.Response(text="<h1>Harness-Zero Web UI not found</h1>", content_type="text/html", status=404)

    async def _handle_artifacts_list(self, request: web.Request) -> web.Response:
        manifest = self.coordinator.artifacts.load_manifest()
        return web.json_response({"manifest": manifest})

    async def _handle_get_artifact(self, request: web.Request) -> web.Response:
        filename = request.match_info.get("filename", "")
        content = self.coordinator.artifacts.get_artifact(filename)
        if content is None:
            return web.Response(text="Artifact not found", status=404)
        return web.Response(text=content, content_type="text/plain")

    async def _handle_spawn_tree(self, request: web.Request) -> web.Response:
        tree = []
        if hasattr(self.coordinator, "spawn_ledger"):
            tree = self.coordinator.spawn_ledger.get_tree()
        return web.json_response({"tree": tree})

    async def _handle_economics(self, request: web.Request) -> web.Response:
        summary = {}
        if hasattr(self.coordinator, "cost_tracker"):
            summary = self.coordinator.cost_tracker.get_summary()
        return web.json_response(summary)

    async def _handle_search_code(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            pattern = body.get("pattern", "")
            matches = await self.coordinator.ripgrep.search(pattern)
            return web.json_response({"matches": [
                {"path": m.file_path, "line": m.line_number, "text": m.line_content}
                for m in matches
            ]})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_settings_get(self, request: web.Request) -> web.Response:
        router = self.coordinator.router
        return web.json_response({
            "configured_providers": {
                p: bool(router.api_keys.get(p))
                for p in ("anthropic", "openai", "gemini", "deepseek", "openrouter")
            },
            "default_provider": router.default_provider,
            "default_model": router.default_model,
            "local_base_url": router.local_base_url,
        })

    async def _handle_settings_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            keys = body.get("api_keys", {})
            provider = body.get("default_provider")
            model = body.get("default_model")
            self.coordinator.router.save_config(
                keys=keys,
                default_provider=provider,
                default_model=model
            )
            return web.json_response({"status": "saved", "active_provider": self.coordinator.router.default_provider})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_login(self, request: web.Request) -> web.Response:
        import time

        token = request.query.get("token")
        if not token or token not in self._login_tokens:
            return web.Response(text="Invalid or expired login link.", status=403)

        expires_at = self._login_tokens.pop(token)
        if time.time() > expires_at:
            return web.Response(text="Login link has expired.", status=403)

        return web.json_response({
            "status": "authenticated",
            "session_id": self.coordinator.session_id,
            "workspace": str(self.coordinator.workspace),
            "message": "Authenticated successfully with single-use token.",
        })

    async def _handle_events(self, request: web.Request) -> web.StreamResponse:
        """Server-Sent Events (SSE) stream for live agent state updates."""
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        # Emit initial session state
        initial_event = json.dumps({
            "type": "SESSION_CONNECTED",
            "session_id": self.coordinator.session_id,
            "step_count": self.coordinator.transcripts.step_counter,
        })
        await response.write(f"data: {initial_event}\n\n".encode("utf-8"))
        return response

    def _setup_sandbox_routes(self) -> None:
        """Port 8001: Untrusted sandbox serving generated artifacts & Generative UI."""
        self.sandbox_app.router.add_get("/artifacts/{filename}", self._handle_sandbox_artifact)
        self.sandbox_app.router.add_get("/health", self._handle_sandbox_health)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "online",
            "workspace": str(self.coordinator.workspace),
            "session_id": self.coordinator.session_id,
            "skills_count": len(self.coordinator.skills.skills),
            "sandbox_port": self.sandbox_port,
        })

    async def _handle_sandbox_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "sandbox_online", "isolated": True})

    async def _handle_sandbox_artifact(self, request: web.Request) -> web.Response:
        filename = request.match_info.get("filename", "")
        content = self.coordinator.artifacts.get_artifact(filename)
        if content is None:
            return web.Response(text="Artifact not found", status=404)

        if filename.endswith(".html"):
            sandboxed_html = self.coordinator.artifacts.render_sandboxed_html(content, title=filename)
            return web.Response(text=sandboxed_html, content_type="text/html")
        elif filename.endswith(".json"):
            return web.Response(text=content, content_type="application/json")
        return web.Response(text=content, content_type="text/plain")

    async def _handle_skills(self, request: web.Request) -> web.Response:
        skills_data = [
            {
                "name": s.name,
                "tier": s.tier,
                "description": s.description,
                "path": str(s.path),
            }
            for s in self.coordinator.skills.list_skills()
        ]
        return web.json_response({"skills": skills_data})

    async def _handle_tasks(self, request: web.Request) -> web.Response:
        tasks = self.coordinator.executor.list_tasks()
        return web.json_response({"tasks": tasks})

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        """JSON-RPC 2.0 handler."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
                status=400,
            )

        rpc_id = body.get("id")
        method = body.get("method")
        params = body.get("params", {})

        if method == "turn.submit":
            prompt = params.get("prompt", "")
            provider = params.get("provider")
            model = params.get("model")
            try:
                res = await self.coordinator.execute_turn(prompt, provider=provider, model=model)
                data = res.to_dict() if hasattr(res, "to_dict") else (res if isinstance(res, dict) else {"output": str(res)})
                return web.json_response({"jsonrpc": "2.0", "result": data, "id": rpc_id})
            except Exception as e:
                return web.json_response(
                    {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": rpc_id},
                    status=500,
                )

        elif method == "architect.submit":
            prompt = params.get("prompt", "")
            files = [Path(f) for f in params.get("files", [])]
            test_cmd = params.get("test_command")
            from harness.orchestration.architect_editor import ArchitectEditorLoop
            loop = ArchitectEditorLoop(
                workspace_root=self.coordinator.workspace,
                router=self.coordinator.router,
            )
            try:
                arch_res = await loop.execute(
                    task_prompt=prompt,
                    target_files=files or list(self.coordinator.workspace.glob("*.py")),
                    test_command=test_cmd,
                )
                return web.json_response({
                    "jsonrpc": "2.0",
                    "result": {
                        "success": arch_res.success,
                        "plan": arch_res.plan,
                        "final_message": arch_res.final_message,
                        "commit_sha": arch_res.commit_sha,
                        "iterations_count": len(arch_res.iterations),
                    },
                    "id": rpc_id,
                })
            except Exception as e:
                return web.json_response(
                    {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": rpc_id},
                    status=500,
                )

        elif method == "memory.search":
            q = params.get("query", "")
            limit = int(params.get("limit", 5))
            results = self.coordinator.memory.search(q, limit=limit)
            return web.json_response({
                "jsonrpc": "2.0",
                "result": [
                    {"step": r.step_index, "snippet": r.snippet, "rank": r.rank}
                    for r in results
                ],
                "id": rpc_id,
            })

        return web.json_response(
            {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method {method} not found"}, "id": rpc_id},
            status=404,
        )

    async def start(self) -> Tuple[web.AppRunner, web.AppRunner]:
        """Starts both Port 8000 API server and Port 8001 SOP Sandbox preview server."""
        # 1. API Runner
        api_runner = web.AppRunner(self.app)
        await api_runner.setup()
        if self.socket_path:
            api_site = web.UnixSite(api_runner, path=str(self.socket_path))
        else:
            api_site = web.TCPSite(api_runner, host=self.host, port=self.port)
        await api_site.start()

        # 2. Sandbox Preview Runner (Port 8001)
        sandbox_runner = web.AppRunner(self.sandbox_app)
        await sandbox_runner.setup()
        sandbox_site = web.TCPSite(sandbox_runner, host=self.host, port=self.sandbox_port)
        await sandbox_site.start()

        logger.info(f"Harness-Zero Core API online at http://{self.host}:{self.port}")
        logger.info(f"Harness-Zero SOP Sandbox Preview online at http://{self.host}:{self.sandbox_port}")

        return api_runner, sandbox_runner

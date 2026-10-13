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

        # Dual-Port Isolation Applications
        self.app = web.Application()
        self.sandbox_app = web.Application()

        self._setup_api_routes()
        self._setup_sandbox_routes()

    def _setup_api_routes(self) -> None:
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_post("/rpc", self._handle_rpc)
        self.app.router.add_get("/skills", self._handle_skills)
        self.app.router.add_get("/tasks", self._handle_tasks)

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
            try:
                res = await self.coordinator.execute_turn(prompt)
                return web.json_response({"jsonrpc": "2.0", "result": {"output": res}, "id": rpc_id})
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

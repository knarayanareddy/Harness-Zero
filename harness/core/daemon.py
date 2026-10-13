"""
Daemon IPC Server for Harness-Zero.
Exposes JSON-RPC 2.0 / REST API over a local Unix domain socket or HTTP port.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

from harness.core.coordinator import AgentCoordinator

logger = logging.getLogger("harness.daemon")


class HarnessDaemon:
    def __init__(
        self,
        coordinator: AgentCoordinator,
        host: str = "127.0.0.1",
        port: int = 8080,
        socket_path: Optional[Path] = None,
    ):
        self.coordinator = coordinator
        self.host = host
        self.port = port
        self.socket_path = socket_path
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_post("/rpc", self._handle_rpc)
        self.app.router.add_get("/skills", self._handle_skills)
        self.app.router.add_get("/tasks", self._handle_tasks)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "online",
            "workspace": str(self.coordinator.workspace),
            "session_id": self.coordinator.session_id,
            "skills_count": len(self.coordinator.skills.skills),
        })

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

    async def start(self) -> web.AppRunner:
        runner = web.AppRunner(self.app)
        await runner.setup()
        if self.socket_path:
            site = web.UnixSite(runner, path=str(self.socket_path))
        else:
            site = web.TCPSite(runner, host=self.host, port=self.port)
        await site.start()
        return runner

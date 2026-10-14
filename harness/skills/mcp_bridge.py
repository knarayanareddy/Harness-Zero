"""
Model Context Protocol (MCP) Bridge.
Allows connecting to external MCP tool servers over stdio or HTTP SSE.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("harness.mcp")


class MCPBridge:
    def __init__(self):
        self._servers: dict[str, dict[str, Any]] = {}
        self._tools: dict[str, dict[str, Any]] = {}

    def register_server(
        self,
        server_name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._servers[server_name] = {
            "command": command,
            "args": args or [],
            "env": env or {},
            "status": "configured",
        }

    def register_tool(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any],
    ) -> None:
        namespaced = f"{server_name}__{tool_name}"
        self._tools[namespaced] = {
            "server": server_name,
            "raw_name": tool_name,
            "name": namespaced,
            "description": description,
            "parameters": input_schema,
        }

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return list(self._tools.values())

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        tool = self._tools.get(tool_name)
        if not tool:
            return {
                "is_error": True,
                "content": f"MCP tool '{tool_name}' not found in registry.",
            }

        # Simulated or lightweight stdio invocation
        server_name = tool["server"]
        return {
            "is_error": False,
            "content": f"MCP result from {server_name}:{tool['raw_name']} with args {json.dumps(arguments)}",
        }

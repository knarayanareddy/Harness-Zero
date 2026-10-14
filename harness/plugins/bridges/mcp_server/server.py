"""
MCPServer: Exposes Harness registered tools over stdio JSON-RPC 2.0 to external MCP hosts (§10, BRG-03).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from harness.sdk.contracts import ToolCall, ToolSpec
from harness.sdk.types import RiskClass, ToolResult

logger = logging.getLogger(__name__)


class MCPServer:
    """
    Stdio JSON-RPC server implementing the Model Context Protocol (MCP).
    Exposes policy-filtered Harness tools to Claude Code, Cursor, Zed, etc.
    """

    def __init__(
        self,
        tools_provider: Callable[[], list[ToolSpec]],
        tool_executor: Callable[[ToolCall], Coroutine[Any, Any, ToolResult]],
        expose_list: list[str] | None = None,
    ) -> None:
        self.tools_provider = tools_provider
        self.tool_executor = tool_executor
        self.expose_list = expose_list

    def get_exposed_tools(self) -> list[ToolSpec]:
        """Filters available tools against expose allow-list if configured."""
        all_tools = self.tools_provider()
        if not self.expose_list:
            return all_tools
        expose_set = set(self.expose_list)
        return [t for t in all_tools if t.name in expose_set]

    async def handle_request(self, req: dict[str, Any]) -> dict[str, Any] | None:
        """Processes a single JSON-RPC 2.0 message."""
        method = req.get("method")
        req_id = req.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {},
                    },
                    "serverInfo": {
                        "name": "harness-zero-mcp-server",
                        "version": "2.0.0",
                    },
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "tools/list":
            exposed = self.get_exposed_tools()
            tool_dicts = []
            for spec in exposed:
                tool_dicts.append({
                    "name": spec.name,
                    "description": spec.description,
                    "inputSchema": spec.input_schema,
                    "annotations": {
                        "readOnly": spec.risk_class == RiskClass.READ,
                        "destructive": spec.risk_class == RiskClass.DESTRUCTIVE,
                        "openWorld": spec.risk_class == RiskClass.NETWORK,
                    },
                })
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tool_dicts},
            }

        if method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            arguments = params.get("arguments", {})

            exposed = self.get_exposed_tools()
            if not any(t.name == name for t in exposed):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Tool '{name}' is not allowed or not found."}],
                        "isError": True,
                    },
                }

            call = ToolCall(
                id=f"mcp_req_{uuid.uuid4().hex[:8]}",
                name=name,
                arguments=arguments,
            )

            try:
                result: ToolResult = await self.tool_executor(call)
                text_parts = []
                for b in result.content:
                    if hasattr(b, "text"):
                        text_parts.append(b.text)
                    else:
                        text_parts.append(str(b))
                combined = "\n".join(text_parts) if text_parts else "Success"
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": combined}],
                        "isError": result.is_error,
                    },
                }
            except Exception as err:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error executing tool '{name}': {err}"}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found"},
        }

    async def run_stdio(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Main stdio loop reading requests and writing responses."""
        while True:
            line = await reader.readline()
            if not line:
                break

            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            try:
                req = json.loads(text)
                res = await self.handle_request(req)
                if res is not None:
                    out = json.dumps(res).encode("utf-8") + b"\n"
                    writer.write(out)
                    await writer.drain()
            except Exception as err:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {err}"},
                }
                writer.write(json.dumps(err_resp).encode("utf-8") + b"\n")
                await writer.drain()


async def run_mcp_server_from_stdio(server: MCPServer) -> None:
    """Helper to run MCPServer over process standard input/output."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
    writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)

    await server.run_stdio(reader, writer)

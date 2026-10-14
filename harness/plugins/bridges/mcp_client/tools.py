"""
Tool generation and execution mapping for MCP tools and resources (§10, BRG-02).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from harness.sdk.context import ToolContext
from harness.sdk.contracts import ToolCall, ToolSpec
from harness.sdk.types import Capability, RiskClass, TextBlock, ToolResult

from .client import MCPClient

logger = logging.getLogger(__name__)


def derive_risk_class(annotations: dict[str, Any] | None) -> RiskClass:
    """
    Maps MCP tool annotations to Harness RiskClass (§10):
    - readOnly -> READ
    - destructive -> DESTRUCTIVE
    - openWorld -> NETWORK
    - else -> WRITE
    """
    if not annotations:
        return RiskClass.WRITE

    if annotations.get("readOnly") or annotations.get("read_only"):
        return RiskClass.READ
    if annotations.get("destructive"):
        return RiskClass.DESTRUCTIVE
    if annotations.get("openWorld") or annotations.get("open_world"):
        return RiskClass.NETWORK

    return RiskClass.WRITE


def create_mcp_tool_spec(server_name: str, mcp_tool: dict[str, Any]) -> ToolSpec:
    """
    Translates an MCP tool definition into a namespaced Harness ToolSpec.
    Namespaced identifier: mcp__{server_name}__{tool_name}
    """
    raw_name = mcp_tool.get("name", "tool")
    namespaced_name = f"mcp__{server_name}__{raw_name}"
    description = mcp_tool.get("description", f"MCP tool '{raw_name}' provided by '{server_name}' server")
    input_schema = mcp_tool.get("inputSchema", {"type": "object", "properties": {}})

    annotations = mcp_tool.get("annotations", {})
    risk = derive_risk_class(annotations)

    caps = frozenset([Capability.NET]) if risk == RiskClass.NETWORK else frozenset()

    return ToolSpec(
        name=namespaced_name,
        description=description,
        input_schema=input_schema,
        risk_class=risk,
        capabilities=caps,
    )


def create_mcp_tool_handler(
    client: MCPClient, original_tool_name: str
) -> Callable[[ToolCall, ToolContext], Any]:
    """Generates an async tool execution handler bridging to MCP client.call_tool()."""

    async def _handler(call: ToolCall, ctx: ToolContext) -> ToolResult:
        try:
            res = await client.call_tool(original_tool_name, call.arguments)
            content_items = res.get("content", [])
            text_blocks: list[TextBlock] = []

            for item in content_items:
                if isinstance(item, dict):
                    t = item.get("text", "")
                    if t:
                        text_blocks.append(TextBlock(text=t))
                elif isinstance(item, str) and item:
                    text_blocks.append(TextBlock(text=item))

            if not text_blocks:
                text_blocks.append(TextBlock(text="Success"))

            is_error = bool(res.get("isError", False))
            return ToolResult(content=text_blocks, is_error=is_error)
        except Exception as err:
            logger.exception(f"Error executing MCP tool '{original_tool_name}': {err}")
            return ToolResult.error(f"MCP tool error: {err}")

    return _handler


# ---------------------------------------------------------------------------
# Built-in read_resource tool for MCP Resources (§10, BRG-02)
# ---------------------------------------------------------------------------

READ_RESOURCE_SPEC = ToolSpec(
    name="read_resource",
    description="Reads content from an external Model Context Protocol resource URI.",
    input_schema={
        "type": "object",
        "properties": {
            "uri": {
                "type": "string",
                "description": "The MCP resource URI to fetch (e.g. memo://today or file:///path)",
            },
            "server": {
                "type": "string",
                "description": "Optional server name if multiple MCP servers are active.",
            },
        },
        "required": ["uri"],
    },
    risk_class=RiskClass.READ,
    capabilities=frozenset(),
)


def create_read_resource_handler(
    clients: dict[str, MCPClient]
) -> Callable[[ToolCall, ToolContext], Any]:
    """Handler for read_resource tool querying active MCP clients."""

    async def _handler(call: ToolCall, ctx: ToolContext) -> ToolResult:
        uri = call.arguments.get("uri")
        if not uri:
            return ToolResult.error("Missing required argument: 'uri'")

        server_target = call.arguments.get("server")
        target_clients = (
            [clients[server_target]]
            if server_target and server_target in clients
            else list(clients.values())
        )

        if not target_clients:
            return ToolResult.error("No active MCP servers connected.")

        last_err: Exception | None = None
        for c in target_clients:
            try:
                res = await c.read_resource(uri)
                contents = res.get("contents", [])
                text_chunks = []
                for item in contents:
                    if isinstance(item, dict) and "text" in item:
                        text_chunks.append(item["text"])
                    elif isinstance(item, str):
                        text_chunks.append(item)
                text = "\n".join(text_chunks) if text_chunks else f"Resource {uri} read successfully."
                return ToolResult(content=[TextBlock(text=text)])
            except Exception as err:
                last_err = err

        return ToolResult.error(f"Failed to read resource '{uri}': {last_err}")

    return _handler

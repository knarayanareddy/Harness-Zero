"""
Model Context Protocol Client Bridge (§10, BRG-02, SIM-01).
"""
from __future__ import annotations

from .client import MCPClient, MCPError
from .plugin import MCPClientBridgePlugin
from .tools import (
    READ_RESOURCE_SPEC,
    create_mcp_tool_handler,
    create_mcp_tool_spec,
    create_read_resource_handler,
    derive_risk_class,
)

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPClientBridgePlugin",
    "READ_RESOURCE_SPEC",
    "create_mcp_tool_handler",
    "create_mcp_tool_spec",
    "create_read_resource_handler",
    "derive_risk_class",
]

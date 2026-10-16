"""
MCPServerBridgePlugin: Plugin exposing Harness tools to external hosts (§10, BRG-03).
"""
from __future__ import annotations

import logging

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .server import MCPServer

logger = logging.getLogger(__name__)


class MCPServerBridgePlugin:
    """
    First-party bridge plugin providing MCP server capabilities.
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.server: MCPServer | None = None

    async def setup(self, ctx: PluginContext) -> None:
        """Configures MCPServer with expose allow-list from settings."""
        settings = getattr(ctx, "settings", None)
        expose_list: list[str] | None = None
        if settings:
            # Own namespace first, then the pre-0.2 spelling kept in older settings files.
            expose_list = settings.get("expose") or settings.get("plugins.mcp-server.expose")

        # Expose provider reading tools from registry if available
        def _get_tools():
            registry = getattr(ctx, "registry", None)
            if registry and hasattr(registry, "tools"):
                return [spec for spec, _, _ in registry.tools.values()]
            return []

        async def _call_tool(call):
            gate = getattr(ctx, "gate", None)
            if gate and hasattr(gate, "execute_tool"):
                return await gate.execute_tool(call)
            return None

        self.server = MCPServer(
            tools_provider=_get_tools,
            tool_executor=_call_tool,
            expose_list=expose_list,
        )

    async def teardown(self) -> None:
        """Teardown server resources."""
        self.server = None

"""
MCPClientBridgePlugin: Bridges external Model Context Protocol servers into Harness (§10, BRG-02, SIM-01).
"""
from __future__ import annotations

import logging
from typing import Any

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .client import MCPClient
from .tools import (
    READ_RESOURCE_SPEC,
    create_mcp_tool_handler,
    create_mcp_tool_spec,
    create_read_resource_handler,
)

logger = logging.getLogger(__name__)


class MCPClientBridgePlugin:
    """
    First-party bridge plugin discovering, connecting, and exposing MCP servers as tools.
    Servers are configured exclusively via settings (none by default).
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.clients: dict[str, MCPClient] = {}

    async def setup(self, ctx: PluginContext) -> None:
        """Connects configured MCP servers and registers discovered tools."""
        settings = getattr(ctx, "settings", None)
        raw_servers: Any = None
        if settings:
            raw_servers = settings.get("plugins.mcp-client.servers") or settings.get("extensions.mcp_servers")

        # Normalize server configurations
        server_configs: list[dict[str, Any]] = []
        if isinstance(raw_servers, dict):
            for name, cfg in raw_servers.items():
                if isinstance(cfg, dict):
                    entry = dict(cfg)
                    entry.setdefault("name", name)
                    server_configs.append(entry)
        elif isinstance(raw_servers, list):
            for cfg in raw_servers:
                if isinstance(cfg, dict):
                    server_configs.append(cfg)

        # Connect each configured server
        for cfg in server_configs:
            name = cfg.get("name")
            command = cfg.get("command")
            if not name or not command:
                continue

            args = cfg.get("args") or []
            env = cfg.get("env") or {}

            client = MCPClient(name=name, command=command, args=args, env=env)
            self.clients[name] = client

            try:
                await client.start()
                tools = await client.list_tools()
                for tool in tools:
                    spec = create_mcp_tool_spec(name, tool)
                    handler = create_mcp_tool_handler(client, tool.get("name", ""))
                    ctx.register_tool(spec, handler)
                logger.info(f"Registered {len(tools)} tools from MCP server '{name}'")
            except Exception as err:
                logger.error(f"Failed to connect MCP server '{name}': {err}")

        # Always register read_resource tool (queries active clients)
        ctx.register_tool(READ_RESOURCE_SPEC, create_read_resource_handler(self.clients))

    async def teardown(self) -> None:
        """Gracefully disconnects all active MCP servers."""
        for name, client in list(self.clients.items()):
            try:
                await client.stop()
            except Exception as err:
                logger.warning(f"Error stopping MCP client '{name}': {err}")
        self.clients.clear()

    def get_status(self) -> dict[str, Any]:
        """Returns diagnostic status dictionary for all configured servers."""
        return {name: client.get_status() for name, client in self.clients.items()}

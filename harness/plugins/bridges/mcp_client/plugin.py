"""MCP client bridge for stdio and Streamable HTTP servers."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from harness.sdk.context import PluginContext
from harness.sdk.events import McpServerStatus, McpToolsChanged, SystemNotice
from harness.sdk.manifest import PluginManifest

from .client import MCPClient
from .tools import (
    READ_RESOURCE_SPEC,
    create_mcp_tool_handler,
    create_mcp_tool_spec,
    create_read_resource_handler,
)

logger = logging.getLogger(__name__)
_SECRET_REFERENCE = re.compile(r"\$\{secrets\.([A-Za-z0-9_.-]+)\}")


class MCPClientBridgePlugin:
    """Connects configured MCP servers and exposes their tools through Harness' policy gate."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.clients: dict[str, MCPClient] = {}

    async def setup(self, ctx: PluginContext) -> None:
        """Connect configured stdio/HTTP servers and register their initial tool lists."""
        settings = getattr(ctx, "settings", None)
        raw_servers: Any = None
        legacy_source: str | None = None
        if settings:
            # The canonical key is the plugin's own `servers` setting. Preserve both historical spellings for
            # one minor version, but announce the migration instead of making the fallback invisible.
            raw_servers = settings.get("servers")
            if not raw_servers:
                raw_servers = settings.get("plugins.mcp-client.servers")
                if raw_servers:
                    legacy_source = "plugins.mcp-client.servers"
            if not raw_servers:
                raw_servers = settings.get("extensions.mcp_servers")
                if raw_servers:
                    legacy_source = "extensions.mcp_servers"
        if legacy_source:
            message = (
                f"MCP server settings at '{legacy_source}' are deprecated; move them to "
                "'plugins.bridge-mcp-client.servers'."
            )
            logger.warning(message)
            emit = getattr(ctx, "emit_event", None)
            if callable(emit):
                emit(SystemNotice(message=message, level="warn", code="mcp.settings.deprecated"))

        server_configs: list[dict[str, Any]] = []
        if isinstance(raw_servers, dict):
            for name, raw in raw_servers.items():
                if isinstance(raw, dict):
                    entry = dict(raw)
                    entry.setdefault("name", name)
                    server_configs.append(entry)
        elif isinstance(raw_servers, list):
            server_configs = [dict(raw) for raw in raw_servers if isinstance(raw, dict)]
        elif raw_servers not in (None, {}):
            logger.error("MCP `servers` setting must be an object or list; ignoring invalid value")

        global_timeout = _positive_float(_setting(settings, "call_timeout_s", 60.0), 60.0)
        global_connect_timeout = _positive_float(_setting(settings, "connect_timeout_s", 15.0), 15.0)
        reconnect = _setting(settings, "reconnect", {}) or {}
        if not isinstance(reconnect, dict):
            reconnect = {}
        reconnect_attempts = _non_negative_int(reconnect.get("max_attempts", 10), 10)
        reconnect_max_backoff = _positive_float(reconnect.get("max_backoff_s", 60.0), 60.0)

        for cfg in server_configs:
            name = str(cfg.get("name") or "").strip()
            if not name:
                logger.error("Skipping MCP server config without a name")
                continue
            if cfg.get("enabled", True) is False:
                logger.info("MCP server '%s' is disabled", name)
                continue

            transport = str(cfg.get("transport") or ("http" if cfg.get("url") else "stdio")).lower()
            try:
                call_timeout = _positive_float(cfg.get("call_timeout_s", global_timeout), global_timeout)
                connect_timeout = _positive_float(
                    cfg.get("connect_timeout_s", global_connect_timeout), global_connect_timeout
                )
                client = self._make_client(
                    ctx=ctx,
                    name=name,
                    cfg=cfg,
                    transport=transport,
                    call_timeout_s=call_timeout,
                    connect_timeout_s=connect_timeout,
                    reconnect_max_attempts=reconnect_attempts,
                    reconnect_max_backoff_s=reconnect_max_backoff,
                )
            except (TypeError, ValueError) as err:
                logger.error("Invalid MCP server config '%s': %s", name, err)
                continue

            self.clients[name] = client
            try:
                await client.start()
                if client.status == "connected":
                    self._register_tools(ctx, client, client.tools)
                    logger.info("Registered %d tools from MCP server '%s'", len(client.tools), name)
                else:
                    logger.warning(
                        "MCP server '%s' is reconnecting; tools will register after its next successful handshake",
                        name,
                    )
            except Exception as err:
                logger.error("Failed to connect MCP server '%s': %s", name, err)

        # Always register the resource helper; it reports when no server is connected.
        ctx.register_tool(READ_RESOURCE_SPEC, create_read_resource_handler(self.clients))

    def _make_client(
        self,
        *,
        ctx: PluginContext,
        name: str,
        cfg: dict[str, Any],
        transport: str,
        call_timeout_s: float,
        connect_timeout_s: float,
        reconnect_max_attempts: int,
        reconnect_max_backoff_s: float,
    ) -> MCPClient:
        if transport == "stdio":
            command = cfg.get("command")
            if not isinstance(command, str) or not command.strip():
                raise ValueError("stdio transport requires a non-empty command")
            args = cfg.get("args") or []
            env = cfg.get("env") or {}
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise ValueError("stdio args must be a list of strings")
            if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
                raise ValueError("stdio env must be an object of string values")
            return MCPClient(
                name=name,
                command=command,
                args=args,
                env=env,
                transport="stdio",
                call_timeout_s=call_timeout_s,
                connect_timeout_s=connect_timeout_s,
                reconnect_max_attempts=reconnect_max_attempts,
                reconnect_max_backoff_s=reconnect_max_backoff_s,
                on_status=lambda status: self._emit_status(ctx, name, status),
                on_tools_changed=lambda client, tools, added, removed: self._tools_changed(
                    ctx, client, tools, added, removed
                ),
            )

        if transport not in {"http", "streamable-http", "streamable_http"}:
            raise ValueError(f"unsupported transport {transport!r}")
        url = cfg.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("HTTP transport requires a non-empty url")
        headers = _resolve_http_headers(ctx, cfg)
        return MCPClient(
            name=name,
            transport="http",
            url=url,
            headers=headers,
            call_timeout_s=call_timeout_s,
            connect_timeout_s=connect_timeout_s,
            reconnect_max_attempts=reconnect_max_attempts,
            reconnect_max_backoff_s=reconnect_max_backoff_s,
            on_status=lambda status: self._emit_status(ctx, name, status),
            on_tools_changed=lambda client, tools, added, removed: self._tools_changed(
                ctx, client, tools, added, removed
            ),
        )

    @staticmethod
    def _register_tools(ctx: PluginContext, client: MCPClient, tools: list[dict[str, Any]]) -> None:
        for tool in tools:
            raw_name = str(tool.get("name") or "")
            if not raw_name:
                continue
            spec = create_mcp_tool_spec(client.name, tool)
            handler = create_mcp_tool_handler(client, raw_name)
            ctx.register_tool(spec, handler)

    async def _tools_changed(
        self,
        ctx: PluginContext,
        client: MCPClient,
        tools: list[dict[str, Any]],
        added: list[str],
        removed: list[str],
    ) -> None:
        """Unregisters removed/changed tools and registers only new or changed definitions."""
        unregister = getattr(ctx, "unregister_tool", None)
        for raw_name in removed:
            if callable(unregister):
                unregister(f"mcp__{client.name}__{raw_name}")
        new_tools = {str(tool.get("name") or ""): tool for tool in tools}
        for raw_name in added:
            tool = new_tools.get(raw_name)
            if tool is None:
                continue
            spec = create_mcp_tool_spec(client.name, tool)
            ctx.register_tool(spec, create_mcp_tool_handler(client, raw_name))

        payload = McpToolsChanged(server=client.name, added=list(added), removed=list(removed))
        content = _tools_changed_text(client.name, added, removed)
        emit_record = getattr(ctx, "emit_record", None)
        if callable(emit_record):
            await emit_record(
                payload,
                content=content,
                metadata={
                    "model_visible": True,
                    "mcp_server": client.name,
                    "added": list(added),
                    "removed": list(removed),
                },
            )
        else:
            emit = getattr(ctx, "emit_event", None)
            if callable(emit):
                emit(payload)

    @staticmethod
    def _emit_status(ctx: PluginContext, server: str, status: dict[str, Any]) -> None:
        emit = getattr(ctx, "emit_event", None)
        if not callable(emit):
            return
        emit(
            McpServerStatus(
                server=server,
                transport=str(status.get("transport", "stdio")),
                state=str(status.get("state", "failed")),  # `error` is normalized to `failed` by MCPClient.
                attempt=int(status.get("attempt", 0) or 0),
                error=status.get("error"),
                tools_count=int(status.get("tools_count", 0) or 0),
                session_id_present=bool(status.get("session_id_present")),
            )
        )

    async def teardown(self) -> None:
        """Gracefully disconnect all configured servers and cancel reconnect tasks."""
        for name, client in list(self.clients.items()):
            try:
                await client.stop()
            except Exception as err:
                logger.warning("Error stopping MCP client '%s': %s", name, err)
        self.clients.clear()

    def get_status(self) -> dict[str, Any]:
        """Returns diagnostic state for each configured server without exposing auth headers."""
        return {name: client.get_status() for name, client in self.clients.items()}


def _setting(settings: Any, key: str, default: Any) -> Any:
    getter = getattr(settings, "get", None)
    return getter(key, default) if callable(getter) else default


def _positive_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _non_negative_int(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(0, number)


def _resolve_http_headers(ctx: PluginContext, cfg: dict[str, Any]) -> dict[str, str]:
    raw = cfg.get("headers") or {}
    if not isinstance(raw, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in raw.items()):
        raise ValueError("HTTP headers must be an object of string values")

    headers: dict[str, str] = {}
    for key, value in raw.items():
        def replace_secret(match: re.Match[str]) -> str:
            name = match.group(1)
            secret = ctx.secrets.get(name)
            if secret is None:
                raise ValueError(f"HTTP header references missing secret '{name}'")
            return secret

        headers[key] = _SECRET_REFERENCE.sub(replace_secret, value)

    auth = cfg.get("auth") or {}
    if auth:
        if not isinstance(auth, dict):
            raise ValueError("HTTP auth must be an object")
        bearer_env = auth.get("bearer_env")
        if bearer_env:
            if not isinstance(bearer_env, str):
                raise ValueError("auth.bearer_env must name an environment variable")
            token = os.environ.get(bearer_env)
            if not token:
                raise ValueError(f"auth.bearer_env environment variable '{bearer_env}' is not set")
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _tools_changed_text(server: str, added: list[str], removed: list[str]) -> str:
    changes = []
    if added:
        changes.append("added " + ", ".join(sorted(added)))
    if removed:
        changes.append("removed " + ", ".join(sorted(removed)))
    return f"MCP server '{server}' tool list changed: {'; '.join(changes)}."

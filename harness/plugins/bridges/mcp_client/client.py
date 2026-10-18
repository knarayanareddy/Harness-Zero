"""Transport-agnostic MCP JSON-RPC client with per-call cancellation and reconnect recovery."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from collections.abc import Callable, Mapping
from itertools import count
from typing import Any

from .supervisor import ReconnectSupervisor
from .transports import MCPTransportError, Transport
from .transports.http import StreamableHTTPTransport
from .transports.stdio import StdioTransport

logger = logging.getLogger(__name__)

# Patterns for sensitive environment variables to scrub before starting a stdio server.
_SECRET_PATTERNS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "AUTH",
    "PRIVATE",
)

StatusCallback = Callable[[dict[str, Any]], Any]
ToolsChangedCallback = Callable[["MCPClient", list[dict[str, Any]], list[str], list[str]], Any]


def _scrub_environment(custom_env: dict[str, str] | None = None) -> dict[str, str]:
    """Provides a safe sanitized environment merged with explicit server env."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(pattern in upper for pattern in _SECRET_PATTERNS):
            continue
        if upper.startswith("AWS_") or upper in ("GH_TOKEN", "GITHUB_TOKEN"):
            continue
        env[key] = value

    env["CI"] = "1"
    env["HARNESS_AGENT"] = "1"
    env["PAGER"] = "cat"
    env["TERM"] = "dumb"

    if custom_env:
        env.update({str(key): str(value) for key, value in custom_env.items()})
    return env


class MCPError(Exception):
    """Raised when an MCP JSON-RPC call returns an error object."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"MCP Error [{code}]: {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPClient:
    """MCP client that owns a transport, request table and bounded reconnect supervisor."""

    def __init__(
        self,
        name: str,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        restart_on_crash: bool = True,
        *,
        transport: str = "stdio",
        url: str | None = None,
        headers: Mapping[str, str] | None = None,
        connect_timeout_s: float = 15.0,
        call_timeout_s: float = 60.0,
        reconnect_max_attempts: int = 10,
        reconnect_backoff_s: float = 1.0,
        reconnect_max_backoff_s: float = 60.0,
        max_backoff_s: float | None = None,
        on_status: StatusCallback | None = None,
        on_tools_changed: ToolsChangedCallback | None = None,
    ) -> None:
        self.name = str(name)
        self.command = command or ""
        self.args = list(args or [])
        self.custom_env = dict(env or {})
        self.transport = str(transport).lower()
        self.url = url
        self.headers = {str(key): str(value) for key, value in (headers or {}).items()}
        self.connect_timeout_s = max(0.1, float(connect_timeout_s))
        self.call_timeout_s = max(0.1, float(call_timeout_s))
        self.restart_on_crash = bool(restart_on_crash)
        self.on_status = on_status
        self.on_tools_changed = on_tools_changed

        if max_backoff_s is not None:
            reconnect_max_backoff_s = max_backoff_s
        self._supervisor = ReconnectSupervisor(
            max_attempts=reconnect_max_attempts,
            initial_backoff_s=reconnect_backoff_s,
            max_backoff_s=reconnect_max_backoff_s,
        )

        self.status = "stopped"  # connecting | connected | reconnecting | error | stopped
        self.last_error: str | None = None
        self.attempt = 0
        self.server_info: dict[str, Any] = {}

        self._transport: Transport | None = None
        # Compatibility/debug surface retained for existing stdio integrations and tests.
        self._proc: asyncio.subprocess.Process | None = None
        self._id_counter = count(1)
        self._pending_requests: dict[int, asyncio.Future[Any]] = {}
        self._tools: list[dict[str, Any]] | None = None
        self._explicitly_stopped = False
        self._disconnect_event: asyncio.Event | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._tool_refresh_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @property
    def tools(self) -> list[dict[str, Any]]:
        """A copy of the most recently discovered tools, safe for plugin registration."""
        return copy.deepcopy(self._tools or [])

    def _make_transport(self) -> Transport:
        if self.transport == "stdio":
            if not self.command:
                raise ValueError(f"stdio MCP server '{self.name}' requires a command")
            return StdioTransport(
                self.command,
                args=self.args,
                env=_scrub_environment(self.custom_env),
            )
        if self.transport in {"http", "streamable-http", "streamable_http"}:
            if not self.url:
                raise ValueError(f"HTTP MCP server '{self.name}' requires a URL")
            return StreamableHTTPTransport(
                self.url,
                headers=self.headers,
                connect_timeout_s=self.connect_timeout_s,
                request_timeout_s=self.call_timeout_s,
            )
        raise ValueError(f"Unsupported MCP transport {self.transport!r} for server '{self.name}'")

    async def start(self) -> None:
        """Starts the transport and initializes MCP; transient initial failures enter backoff."""
        if self.status == "connected":
            return
        self._explicitly_stopped = False
        self._disconnect_event = asyncio.Event()
        self.attempt = 0
        await self._set_status("connecting", error=None)
        try:
            await self._connect(initial=True)
        except (ValueError, TypeError) as err:
            await self._close_transport()
            await self._set_status("error", error=str(err))
            raise
        except Exception as err:
            self.last_error = str(err)
            await self._close_transport()
            if self.restart_on_crash and self._supervisor.max_attempts > 0:
                await self._set_status("reconnecting", error=str(err))
                if self._disconnect_event is not None:
                    self._disconnect_event.set()
                self._supervisor_task = asyncio.create_task(
                    self._supervise(), name=f"mcp-supervisor-{self.name}"
                )
                return
            await self._set_status("error", error=str(err))
            raise
        self._supervisor_task = asyncio.create_task(
            self._supervise(), name=f"mcp-supervisor-{self.name}"
        )

    async def _connect(self, *, initial: bool) -> None:
        """Creates a fresh transport, initializes the protocol and fetches the tool list."""
        await self._close_transport()
        transport = self._make_transport()
        self._transport = transport
        await transport.start(self._on_message, self._on_transport_disconnect)
        self._proc = getattr(transport, "process", None)

        init_result = await self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {},
                    "prompts": {},
                },
                "clientInfo": {"name": "harness-zero", "version": "2.0.0"},
            },
            timeout=self.connect_timeout_s,
            allow_not_connected=True,
        )
        self.server_info = dict(init_result.get("serverInfo") or {}) if isinstance(init_result, dict) else {}
        await self._send_notification(
            "notifications/initialized",
            {},
            allow_not_connected=True,
        )
        await transport.open_event_stream()

        try:
            current_tools = await self._fetch_tools(allow_not_connected=True)
        except MCPError as err:
            if err.code != -32601:
                raise
            # A server may implement another MCP feature without advertising tools.
            current_tools = []

        if initial or self._tools is None:
            self._tools = copy.deepcopy(current_tools)
            await self._set_status("connected", error=None)
            return

        # Publish connected before invoking plugin callbacks; calls are now safe to make against this session.
        await self._set_status("connected", error=None)
        await self._apply_tool_list(current_tools)

    async def _close_transport(self) -> None:
        transport, self._transport = self._transport, None
        self._proc = None
        if transport is not None:
            try:
                await transport.close()
            except Exception as err:
                logger.debug("Error closing MCP transport '%s': %s", self.name, err)

    async def _set_status(self, status: str, *, error: str | None = None) -> None:
        self.status = status
        if status == "connected":
            self.last_error = None
            self.attempt = 0
        elif error is not None:
            self.last_error = error
        if self.on_status is not None:
            try:
                outcome = self.on_status(self.get_status())
                if hasattr(outcome, "__await__"):
                    await outcome
            except Exception as err:
                logger.debug("MCP status callback for '%s' failed: %s", self.name, err)

    async def _on_transport_disconnect(self, error: Exception) -> None:
        if self._explicitly_stopped:
            return
        await self._mark_disconnected(error)

    async def _mark_disconnected(self, error: Exception) -> None:
        if self._explicitly_stopped:
            return
        # A GET-stream failure and a simultaneous POST failure describe one disconnect, not two retries.
        if self.status == "reconnecting" and self._disconnect_event is not None and self._disconnect_event.is_set():
            return
        self.last_error = str(error)
        if self.restart_on_crash and self._supervisor.max_attempts > 0:
            await self._set_status("reconnecting", error=str(error))
        else:
            await self._set_status("error", error=str(error))
        reset = ConnectionError(
            f"MCP server '{self.name}' is {self.get_status()['state']}"
            + (f" (attempt {self.attempt})" if self.attempt else "")
            + "; try again shortly"
        )
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(reset)
        self._pending_requests.clear()
        if self._disconnect_event is not None:
            self._disconnect_event.set()

    async def _supervise(self) -> None:
        """Waits for disconnects and retries until the configured attempt budget is exhausted."""
        event = self._disconnect_event
        if event is None:
            return
        while not self._explicitly_stopped:
            await event.wait()
            event.clear()
            if self._explicitly_stopped:
                return
            if not self.restart_on_crash:
                await self._set_status("error", error=self.last_error)
                return

            recovered = False
            for attempt in range(1, self._supervisor.max_attempts + 1):
                if self._explicitly_stopped:
                    return
                self.attempt = attempt
                await self._set_status("reconnecting", error=self.last_error)
                await asyncio.sleep(self._supervisor.delay(attempt))
                if self._explicitly_stopped:
                    return
                try:
                    await self._connect(initial=False)
                    recovered = True
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    self.last_error = str(err)
                    await self._close_transport()
                    await self._set_status("reconnecting", error=str(err))
                    # This supervisor already owns the next retry; discard the disconnect signal produced by
                    # the failed handshake so it does not trigger a second, redundant retry cycle afterward.
                    event.clear()
            if recovered:
                continue
            if not self._explicitly_stopped:
                await self._set_status("error", error=self.last_error or "Reconnect attempts exhausted")
            return

    def _schedule(self, coroutine: Any, *, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _on_message(self, message: dict[str, Any]) -> None:
        """Correlates responses and handles server notifications from either transport."""
        method = message.get("method")
        if method and message.get("id") is None:
            if method == "notifications/tools/list_changed":
                self._schedule(self._refresh_tool_list(), name=f"mcp-tools-refresh-{self.name}")
            return

        request_id = message.get("id")
        if request_id is None:
            return
        future = self._pending_requests.pop(request_id, None)
        if future is None or future.done():
            # Late response to a cancelled/timed-out request, or an unrelated server response.
            return
        error = message.get("error")
        if isinstance(error, dict) and error:
            future.set_exception(
                MCPError(
                    code=int(error.get("code", -1)),
                    message=str(error.get("message", "Unknown error")),
                    data=error.get("data"),
                )
            )
        else:
            future.set_result(message.get("result", {}))

    async def _refresh_tool_list(self) -> None:
        if self.status not in {"connected", "reconnecting"} or self._transport is None:
            return
        async with self._tool_refresh_lock:
            try:
                tools = await self._fetch_tools(allow_not_connected=self.status != "connected")
            except Exception as err:
                if isinstance(err, (ConnectionError, MCPTransportError)):
                    await self._mark_disconnected(err)
                else:
                    logger.warning("Could not refresh tools from MCP server '%s': %s", self.name, err)
                return
            await self._apply_tool_list(tools)

    async def _apply_tool_list(self, tools: list[dict[str, Any]]) -> None:
        old = self._tools or []
        old_by_name = {str(tool.get("name", "")): tool for tool in old}
        new_by_name = {str(tool.get("name", "")): tool for tool in tools}
        removed = [
            name
            for name, old_tool in old_by_name.items()
            if name not in new_by_name or _canonical(old_tool) != _canonical(new_by_name[name])
        ]
        added = [
            name
            for name, new_tool in new_by_name.items()
            if name not in old_by_name or _canonical(old_by_name[name]) != _canonical(new_tool)
        ]
        self._tools = copy.deepcopy(tools)
        if not added and not removed:
            return
        if self.on_tools_changed is not None:
            try:
                outcome = self.on_tools_changed(self, copy.deepcopy(tools), added, removed)
                if hasattr(outcome, "__await__"):
                    await outcome
            except Exception as err:
                logger.exception("MCP tool-list callback for '%s' failed: %s", self.name, err)

    async def _fetch_tools(self, *, allow_not_connected: bool = False) -> list[dict[str, Any]]:
        response = await self.request(
            "tools/list",
            timeout=self.connect_timeout_s if allow_not_connected else None,
            allow_not_connected=allow_not_connected,
        )
        tools = response.get("tools", []) if isinstance(response, dict) else []
        if not isinstance(tools, list):
            raise MCPError(-32603, "MCP tools/list returned a non-list tools field")
        return [tool for tool in tools if isinstance(tool, dict)]

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        *,
        cancel_event: asyncio.Event | None = None,
        allow_not_connected: bool = False,
    ) -> Any:
        """Sends a JSON-RPC request and waits for the response or a cancellation/timeout."""
        if not allow_not_connected and self.status != "connected":
            state = self.get_status()["state"]
            detail = f" (attempt {self.attempt})" if self.attempt else ""
            raise ConnectionError(f"MCP server '{self.name}' is {state}{detail}; try again shortly")
        transport = self._transport
        if transport is None or not transport.alive:
            raise ConnectionError(f"MCP server '{self.name}' is not connected")

        request_id = next(self._id_counter)
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        send_task = asyncio.create_task(transport.send(message), name=f"mcp-send-{self.name}-{request_id}")
        cancel_task = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
        timeout_s = self.call_timeout_s if timeout is None else max(0.001, float(timeout))
        deadline = asyncio.get_running_loop().time() + timeout_s
        cancel_sent = False

        try:
            while True:
                if future.done():
                    return future.result()
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"MCP {method} call to '{self.name}' timed out after {timeout_s:g}s")
                waiting: set[asyncio.Future[Any] | asyncio.Task[Any]] = {future}
                if send_task is not None:
                    waiting.add(send_task)
                if cancel_task is not None:
                    waiting.add(cancel_task)
                done, _ = await asyncio.wait(
                    waiting,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    raise TimeoutError(f"MCP {method} call to '{self.name}' timed out after {timeout_s:g}s")
                if future in done:
                    return future.result()
                if cancel_task is not None and cancel_task in done and cancel_event and cancel_event.is_set():
                    await self._cancel_request(request_id, future, send_task, "cancelled by harness")
                    cancel_sent = True
                    raise asyncio.CancelledError
                if send_task is not None and send_task in done:
                    try:
                        send_task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception as err:
                        await self._mark_disconnected(err)
                        if future.done() and not future.cancelled():
                            # `_mark_disconnected` fails all in-flight futures; this request raises the original
                            # send error instead, so consume its duplicate future exception explicitly.
                            future.exception()
                        raise
                    send_task = None
        except TimeoutError:
            await self._cancel_request(request_id, future, send_task, "MCP call timed out")
            raise
        except asyncio.CancelledError:
            # Covers direct task cancellation as well as the explicit cancel_event above.
            if not cancel_sent:
                await self._cancel_request(request_id, future, send_task, "cancelled by harness")
            raise
        finally:
            self._pending_requests.pop(request_id, None)
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)

    async def _cancel_request(
        self,
        request_id: int,
        future: asyncio.Future[Any],
        send_task: asyncio.Task[Any] | None,
        reason: str,
    ) -> None:
        self._pending_requests.pop(request_id, None)
        if not future.done():
            future.cancel()
        if send_task is not None and not send_task.done():
            send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)
        # Best effort: the server may already have completed, but any later result is now uncorrelated.
        try:
            await asyncio.wait_for(
                self._send_notification(
                    "notifications/cancelled",
                    {"requestId": request_id, "reason": reason},
                ),
                timeout=min(1.0, self.connect_timeout_s),
            )
        except Exception as err:
            logger.debug("Could not notify MCP server '%s' of cancellation: %s", self.name, err)

    async def _send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        allow_not_connected: bool = False,
    ) -> None:
        transport = self._transport
        if transport is None or not transport.alive:
            return
        if not allow_not_connected and self.status != "connected":
            return
        await transport.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Sends a JSON-RPC notification (no response expected)."""
        try:
            await self._send_notification(method, params)
        except Exception as err:
            await self._mark_disconnected(err)
            raise

    async def list_tools(self) -> list[dict[str, Any]]:
        """Queries tools and updates the cache; callbacks run only when the list actually changes."""
        tools = await self._fetch_tools()
        await self._apply_tool_list(tools)
        return copy.deepcopy(tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        """Calls an MCP tool with the configured timeout and cooperative cancellation signal."""
        result = await self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=self.call_timeout_s if timeout is None else timeout,
            cancel_event=cancel_event,
        )
        return result if isinstance(result, dict) else {}

    async def list_resources(self) -> list[dict[str, Any]]:
        response = await self.request("resources/list")
        values = response.get("resources", []) if isinstance(response, dict) else []
        return values if isinstance(values, list) else []

    async def read_resource(self, uri: str) -> dict[str, Any]:
        response = await self.request("resources/read", {"uri": uri})
        return response if isinstance(response, dict) else {}

    async def list_prompts(self) -> list[dict[str, Any]]:
        response = await self.request("prompts/list")
        values = response.get("prompts", []) if isinstance(response, dict) else []
        return values if isinstance(values, list) else []

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.request("prompts/get", {"name": name, "arguments": arguments or {}})
        return response if isinstance(response, dict) else {}

    async def stop(self) -> None:
        """Stops reconnect and closes the underlying process/session."""
        self._explicitly_stopped = True
        if self._disconnect_event is not None:
            self._disconnect_event.set()
        supervisor, self._supervisor_task = self._supervisor_task, None
        if supervisor is not None and not supervisor.done():
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        failure = ConnectionError(f"MCP server '{self.name}' was stopped")
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(failure)
        self._pending_requests.clear()
        await self._close_transport()
        await self._set_status("stopped", error=None)

    def get_status(self) -> dict[str, Any]:
        """Returns connection diagnostics without exposing secret headers or session identifiers."""
        state = "failed" if self.status == "error" else self.status
        pid = self._proc.pid if self._proc is not None and self._proc.returncode is None else None
        return {
            "name": self.name,
            "transport": self.transport,
            "status": self.status,
            "state": state,
            "attempt": self.attempt,
            "last_error": self.last_error,
            "error": self.last_error,
            "tools": len(self._tools or []),
            "tools_count": len(self._tools or []),
            "session_id_present": bool(getattr(self._transport, "session_id", None)),
            "pid": pid,
        }


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

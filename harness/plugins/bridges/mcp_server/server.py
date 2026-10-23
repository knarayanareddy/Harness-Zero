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


class _MCPOutputFlushTimeout(Exception):
    """The stdio peer stopped accepting a response before its drain deadline."""


def _validated_expose_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
        raise ValueError("MCP expose allow-list must be a list of tool name strings")
    return list(value)


def resolve_expose_list(settings: Any) -> list[str]:
    """Canonical explicit values (including empty/null) beat the legacy spelling."""
    if settings is None:
        return []
    missing = object()
    # A supplied sentinel distinguishes an absent key from a schema default.
    # This works with both SettingsStore and the plugin's public namespaced view.
    value = settings.get("plugins.bridge-mcp-server.expose", missing)
    if value is missing:
        value = settings.get("plugins.mcp-server.expose", [])
    return _validated_expose_list(value)


class MCPServer:
    """
    Stdio JSON-RPC server implementing the Model Context Protocol (MCP).
    Exposes policy-filtered Harness tools to Claude Code, Cursor, Zed, etc.
    """

    # A finite per-stdio-session admission budget bounds retained request IDs.
    # It is an internal connection-lifetime limit, not a user setting.
    _MAX_REQUEST_IDS_PER_CONNECTION = 4096
    # A half-close stops admission, but gives an already accepted short call a
    # bounded chance to flush its response before cancelling remaining work.
    _EOF_DRAIN_GRACE_S = 0.1
    # Bound every output flush so a peer that stops reading cannot stall the
    # serial protocol loop. EOF uses the smaller remaining part of its drain grace.
    _STDIO_WRITE_DRAIN_TIMEOUT_S = 1.0

    def __init__(
        self,
        tools_provider: Callable[[], list[ToolSpec]],
        tool_executor: Callable[[ToolCall], Coroutine[Any, Any, ToolResult]],
        expose_list: list[str] | None = None,
        cancellable_tool_executor: Callable[
            [ToolCall, asyncio.Event], Coroutine[Any, Any, ToolResult]
        ] | None = None,
    ) -> None:
        self.tools_provider = tools_provider
        self.tool_executor = tool_executor
        self.cancellable_tool_executor = cancellable_tool_executor
        self.expose_list = _validated_expose_list(expose_list)

    def get_exposed_tools(self) -> list[ToolSpec]:
        """Filters available tools against an allow-list; missing/empty means none."""
        all_tools = self.tools_provider()
        expose_set = set(self.expose_list or ())
        return [t for t in all_tools if t.name in expose_set]

    async def handle_request(
        self,
        req: dict[str, Any],
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any] | None:
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
            if not isinstance(params, dict):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Invalid params: expected an object"},
                }
            name = params.get("name")

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

            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Invalid params: tool arguments must be an object"},
                }

            call = ToolCall(
                id=f"mcp_req_{uuid.uuid4().hex}",
                name=name,
                arguments=arguments,
            )

            try:
                if cancel_event is not None and self.cancellable_tool_executor is not None:
                    result: ToolResult = await self.cancellable_tool_executor(call, cancel_event)
                else:
                    result = await self.tool_executor(call)
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

    @staticmethod
    def _request_id_key(request_id: Any) -> tuple[type, str | int] | None:
        """Keep valid MCP string/integer IDs distinct; bool is not an integer ID."""
        if type(request_id) is str or type(request_id) is int:
            return type(request_id), request_id
        return None

    @classmethod
    def _cancel_request_id(cls, req: dict[str, Any]) -> tuple[type, str | int] | None:
        """Returns a well-formed cancellation target, or None for malformed notifications."""
        if req.get("jsonrpc") != "2.0" or "id" in req:
            return None
        params = req.get("params")
        if not isinstance(params, dict) or "requestId" not in params:
            return None
        if "reason" in params and not isinstance(params["reason"], str):
            return None
        return cls._request_id_key(params["requestId"])

    @staticmethod
    async def _write_response(
        writer: asyncio.StreamWriter,
        response: dict[str, Any] | None,
        *,
        timeout_s: float | None = None,
    ) -> None:
        if response is None:
            return
        writer.write(json.dumps(response).encode("utf-8") + b"\n")
        timeout = MCPServer._STDIO_WRITE_DRAIN_TIMEOUT_S
        if timeout_s is not None:
            timeout = min(timeout, max(0.0, timeout_s))
        try:
            await asyncio.wait_for(writer.drain(), timeout=timeout)
        except TimeoutError as err:
            transport = getattr(writer, "transport", None)
            abort = getattr(transport, "abort", None)
            if callable(abort):
                abort()
            raise _MCPOutputFlushTimeout("MCP stdio response drain timed out") from err

    async def run_stdio(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve line-framed JSON-RPC with one cancellable tool call at a time."""
        read_task: asyncio.Task[bytes] | None = asyncio.create_task(reader.readline())
        active_task: asyncio.Task[dict[str, Any] | None] | None = None
        active_id: Any = None
        active_id_key: tuple[type, str | int] | None = None
        active_cancel_event: asyncio.Event | None = None
        active_cancelable = False
        active_cancelled = False
        admitted_request_ids: set[tuple[type, str | int]] = set()
        request_id_budget_exhausted = False

        async def finish_active(*, deadline: float | None = None) -> None:
            nonlocal active_task, active_id, active_id_key
            nonlocal active_cancel_event, active_cancelable, active_cancelled
            task = active_task
            if task is None:
                return
            request_id = active_id
            suppress_response = active_cancelled
            active_task = None
            active_id = None
            active_id_key = None
            active_cancel_event = None
            active_cancelable = False
            active_cancelled = False
            try:
                response = task.result()
            except asyncio.CancelledError:
                response = None
            except Exception:
                logger.exception("MCP tool request failed")
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": "Internal error"},
                }
            if not suppress_response:
                remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
                await self._write_response(writer, response, timeout_s=remaining)

        async def stop_active_for_eof() -> None:
            nonlocal active_cancelled
            if active_task is None:
                return
            deadline = asyncio.get_running_loop().time() + self._EOF_DRAIN_GRACE_S
            if not active_cancelled:
                done, _ = await asyncio.wait(
                    {active_task}, timeout=max(0.0, deadline - asyncio.get_running_loop().time())
                )
                if done:
                    await finish_active(deadline=deadline)
                    return
            if active_cancelable and active_cancel_event is not None:
                active_cancelled = True
                active_cancel_event.set()
            else:
                active_task.cancel()
            await asyncio.gather(active_task, return_exceptions=True)
            await finish_active()

        async def stop_active_for_id_budget() -> None:
            """Settle active work with an explicit error when IDs cannot be admitted."""
            nonlocal active_task, active_id, active_id_key
            nonlocal active_cancel_event, active_cancelable, active_cancelled
            task = active_task
            if task is None:
                return
            if task.done():
                await finish_active()
                return
            request_id = active_id
            suppress_response = active_cancelled
            if active_cancelable and active_cancel_event is not None:
                active_cancel_event.set()
            else:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            active_task = None
            active_id = None
            active_id_key = None
            active_cancel_event = None
            active_cancelable = False
            active_cancelled = False
            if not suppress_response:
                await self._write_response(
                    writer,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32000,
                            "message": "Request ID admission budget exhausted; active tool call was stopped",
                        },
                    },
                )

        try:
            while read_task is not None:
                waiters: set[asyncio.Task[Any]] = {read_task}
                if active_task is not None:
                    waiters.add(active_task)
                done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)

                # EOF establishes the shared drain deadline before a completed
                # call can enter its response flush, including a same-turn race.
                if read_task in done and not read_task.result():
                    read_task = None
                    await stop_active_for_eof()
                    break

                # Completion wins a same-turn race with a late cancellation notification.
                if active_task is not None and active_task in done:
                    await finish_active()

                if read_task not in done:
                    continue
                line = read_task.result()
                read_task = None
                if not line:
                    await stop_active_for_eof()
                    break

                try:
                    req = json.loads(line.decode("utf-8", errors="replace").strip())
                    if not isinstance(req, dict):
                        raise ValueError("JSON-RPC request must be an object")
                except Exception as err:
                    await self._write_response(
                        writer,
                        {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {err}"}},
                    )
                    read_task = asyncio.create_task(reader.readline())
                    continue

                method = req.get("method")
                if method == "notifications/cancelled":
                    if active_task is not None and active_task.done():
                        await finish_active()
                    target_key = self._cancel_request_id(req)
                    if (
                        target_key is not None
                        and active_task is not None
                        and active_id_key == target_key
                        and active_cancelable
                        and active_cancel_event is not None
                        and not active_task.done()
                    ):
                        active_cancelled = True
                        active_cancel_event.set()
                    # Cancellation is a notification, including malformed/late/unknown cases.
                else:
                    if active_task is not None and active_task.done():
                        await finish_active()
                    request_id = req.get("id")
                    request_id_key = self._request_id_key(request_id) if "id" in req else None
                    admission_error: dict[str, Any] | None = None
                    if (
                        ("id" not in req and method != "notifications/initialized")
                        or ("id" in req and request_id_key is None)
                    ):
                        admission_error = {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32600, "message": "Invalid request id"},
                        }
                    elif request_id_key is not None and request_id_budget_exhausted:
                        admission_error = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32000, "message": "Request ID admission budget exhausted"},
                        }
                    elif request_id_key is not None and request_id_key in admitted_request_ids:
                        admission_error = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32600, "message": "Request ID has already been used in this session"},
                        }
                    elif (
                        request_id_key is not None
                        and len(admitted_request_ids) >= self._MAX_REQUEST_IDS_PER_CONNECTION
                    ):
                        request_id_budget_exhausted = True
                        await stop_active_for_id_budget()
                        admission_error = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32000, "message": "Request ID admission budget exhausted"},
                        }
                    elif request_id_key is not None:
                        admitted_request_ids.add(request_id_key)

                    if admission_error is not None:
                        await self._write_response(writer, admission_error)
                    elif method == "tools/call":
                        request_id = req.get("id")
                        request_id_key = self._request_id_key(request_id)
                        if request_id_key is None:
                            await self._write_response(
                                writer,
                                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request id"}},
                            )
                        elif active_task is not None:
                            await self._write_response(
                                writer,
                                {
                                    "jsonrpc": "2.0",
                                    "id": request_id,
                                    "error": {"code": -32000, "message": "A tool call is already in progress"},
                                },
                            )
                        else:
                            active_id = request_id
                            active_id_key = request_id_key
                            active_cancel_event = asyncio.Event()
                            active_cancelable = self.cancellable_tool_executor is not None
                            active_cancelled = False
                            active_task = asyncio.create_task(
                                self.handle_request(req, cancel_event=active_cancel_event)
                            )
                    else:
                        try:
                            response = await self.handle_request(req)
                        except Exception as err:
                            response = {
                                "jsonrpc": "2.0",
                                "id": req.get("id"),
                                "error": {"code": -32603, "message": "Internal error"},
                            }
                            logger.exception("MCP request failed: %s", err)
                        await self._write_response(writer, response)

                read_task = asyncio.create_task(reader.readline())
        except _MCPOutputFlushTimeout:
            # The transport is aborted by _write_response; don't admit more work
            # after a response became undeliverable.
            logger.debug("MCP stdio output stalled; closing the session")
        finally:
            if read_task is not None and not read_task.done():
                read_task.cancel()
                await asyncio.gather(read_task, return_exceptions=True)
            if active_task is not None:
                if active_cancelable and active_cancel_event is not None:
                    active_cancelled = True
                    active_cancel_event.set()
                else:
                    active_task.cancel()
                await asyncio.gather(active_task, return_exceptions=True)
                try:
                    await finish_active()
                except _MCPOutputFlushTimeout:
                    logger.debug("MCP stdio output stalled during final cleanup")


async def run_mcp_server_from_stdio(server: MCPServer) -> None:
    """Helper to run MCPServer over process standard input/output."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
    writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)

    await server.run_stdio(reader, writer)

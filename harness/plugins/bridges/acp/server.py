"""
ACPServer: Agent Client Protocol stdio server for editor integration (§10, BRG-04).
Bridges editor requests (session/new, prompt, cancel, approvals) to Harness Commands and EventBus.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from .protocol import ACPNotification, ACPResponse

logger = logging.getLogger(__name__)


class ACPServer:
    """
    Stdio JSON-RPC 2.0 Agent Client Protocol frontend for editors.
    Bridges tool-call permission requests ↔ approvals and streaming content updates ↔ deltas.
    """

    def __init__(self, commands: Any, bus: Any | None = None) -> None:
        self.commands = commands
        self.bus = bus
        self._writer: asyncio.StreamWriter | None = None
        self._bus_task: asyncio.Task[None] | None = None
        self._subscriber = None

    async def handle_request(self, req: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatches an incoming JSON-RPC request to its ACP method handler."""
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {}) or {}

        if method == "initialize":
            return ACPResponse(
                id=req_id,
                result={
                    "protocolVersion": "0.1",
                    "capabilities": {
                        "streaming": True,
                        "approvals": True,
                        "interrupt": True,
                    },
                    "serverInfo": {
                        "name": "harness-zero-acp",
                        "version": "2.0.0",
                    },
                },
            ).to_dict()

        if method == "notifications/initialized":
            return None

        if method == "session/new":
            session_id = params.get("session_id")
            title = params.get("title") or "ACP Editor Session"
            try:
                res = await self.commands.session_new(session_id=session_id, title=title)
                return ACPResponse(id=req_id, result=res).to_dict()
            except Exception as err:
                return ACPResponse(
                    id=req_id,
                    error={"code": -32000, "message": f"Failed to create session: {err}"},
                ).to_dict()

        if method == "prompt":
            prompt_text = params.get("prompt", "")
            session_id = params.get("session_id")
            options = params.get("options") or {}
            try:
                turn_result = await self.commands.run_turn(
                    prompt_text,
                    session_id=session_id,
                    options=options,
                )
                output_str = getattr(turn_result, "output", "") or ""
                return ACPResponse(
                    id=req_id,
                    result={
                        "status": "completed",
                        "output": output_str,
                        "session_id": session_id,
                    },
                ).to_dict()
            except Exception as err:
                return ACPResponse(
                    id=req_id,
                    error={"code": -32000, "message": f"Turn execution failed: {err}"},
                ).to_dict()

        if method == "cancel":
            session_id = params.get("session_id")
            try:
                res = await self.commands.interrupt(session_id=session_id)
                return ACPResponse(id=req_id, result=res).to_dict()
            except Exception as err:
                return ACPResponse(
                    id=req_id,
                    error={"code": -32000, "message": f"Interrupt failed: {err}"},
                ).to_dict()

        if method in ("approval/resolve", "permission/resolve"):
            app_id = params.get("request_id") or params.get("approval_id")
            approved = bool(params.get("approved", params.get("decision") == "approve"))
            reason = params.get("reason", "")
            try:
                res = await self.commands.resolve_approval(
                    approval_id=app_id,
                    approved=approved,
                    reason=reason,
                )
                return ACPResponse(id=req_id, result=res).to_dict()
            except Exception as err:
                return ACPResponse(
                    id=req_id,
                    error={"code": -32000, "message": f"Failed to resolve approval: {err}"},
                ).to_dict()

        if method == "session/list":
            try:
                res = await self.commands.session_list()
                return ACPResponse(id=req_id, result=res).to_dict()
            except Exception as err:
                return ACPResponse(
                    id=req_id,
                    error={"code": -32000, "message": f"Failed to list sessions: {err}"},
                ).to_dict()

        return ACPResponse(
            id=req_id,
            error={"code": -32601, "message": f"Method '{method}' not found"},
        ).to_dict()

    async def _forward_bus_events(self) -> None:
        """Forwards kernel event bus notifications to the connected editor client."""
        if not self.bus or not self._writer:
            return

        self._subscriber = self.bus.subscribe()
        try:
            while True:
                evt = await self._subscriber.get()
                if not self._writer or self._writer.is_closing():
                    break

                notif = None
                if evt.type == "model.text.delta":
                    notif = ACPNotification(
                        method="turn/delta",
                        params={"session_id": evt.session_id, "delta": evt.data.get("text", "")},
                    )
                elif evt.type == "model.thinking.delta":
                    notif = ACPNotification(
                        method="turn/thinking",
                        params={"session_id": evt.session_id, "delta": evt.data.get("text", "")},
                    )
                elif evt.type == "tool.call":
                    notif = ACPNotification(
                        method="turn/tool_call",
                        params={
                            "session_id": evt.session_id,
                            "call_id": evt.data.get("call_id"),
                            "name": evt.data.get("name"),
                            "arguments": evt.data.get("arguments"),
                        },
                    )
                elif evt.type == "tool.result":
                    notif = ACPNotification(
                        method="turn/tool_result",
                        params={
                            "session_id": evt.session_id,
                            "call_id": evt.data.get("call_id"),
                            "is_error": evt.data.get("is_error", False),
                        },
                    )
                elif evt.type == "approval.required":
                    notif = ACPNotification(
                        method="permission/request",
                        params={
                            "session_id": evt.session_id,
                            "request_id": evt.data.get("approval_id") or evt.data.get("request_id"),
                            "action": evt.data.get("action"),
                            "risk": evt.data.get("risk_class") or evt.data.get("risk"),
                            "details": evt.data.get("details"),
                        },
                    )
                elif evt.type == "turn.completed":
                    notif = ACPNotification(
                        method="turn/completed",
                        params={"session_id": evt.session_id, "turn_id": evt.turn_id},
                    )

                if notif and self._writer and not self._writer.is_closing():
                    line = json.dumps(notif.to_dict()).encode("utf-8") + b"\n"
                    self._writer.write(line)
                    await self._writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as err:
            logger.warning(f"Error in ACP bus forwarding: {err}")
        finally:
            if self.bus and self._subscriber:
                self.bus.unsubscribe(self._subscriber)
                self._subscriber = None

    async def run_stdio(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Runs the JSON-RPC event loop over stdio reader and writer."""
        self._writer = writer
        if self.bus:
            self._bus_task = asyncio.create_task(self._forward_bus_events())

        try:
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
                    if res is not None and not writer.is_closing():
                        out = json.dumps(res).encode("utf-8") + b"\n"
                        writer.write(out)
                        await writer.drain()
                except Exception as parse_err:
                    err_res = ACPResponse(
                        id=None,
                        error={"code": -32700, "message": f"Parse error: {parse_err}"},
                    ).to_dict()
                    writer.write(json.dumps(err_res).encode("utf-8") + b"\n")
                    await writer.drain()
        finally:
            if self._bus_task and not self._bus_task.done():
                self._bus_task.cancel()
            self._writer = None


async def run_acp_server_from_stdio(commands: Any, bus: Any | None = None) -> None:
    """Helper to start ACP server listening over standard input/output."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
    writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)

    server = ACPServer(commands=commands, bus=bus)
    await server.run_stdio(reader, writer)

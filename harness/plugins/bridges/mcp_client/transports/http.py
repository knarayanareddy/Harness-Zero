"""MCP Streamable HTTP transport (JSON request/response and optional SSE streams)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from . import DisconnectHandler, JSONRPCMessage, MCPTransportError, MessageHandler

logger = logging.getLogger(__name__)


class StreamableHTTPTransport:
    """MCP Streamable HTTP transport with session and server-notification support."""

    name = "http"

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        connect_timeout_s: float = 15.0,
        request_timeout_s: float = 60.0,
    ) -> None:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("HTTP MCP transport requires a non-empty URL")
        self.url = url
        self.headers = {str(key): str(value) for key, value in (headers or {}).items()}
        self.connect_timeout_s = max(0.1, float(connect_timeout_s))
        self.request_timeout_s = max(0.1, float(request_timeout_s))
        self.client: httpx.AsyncClient | None = None
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.alive = False
        self._closing = False
        self._event_task: asyncio.Task[None] | None = None
        self._on_message: MessageHandler | None = None
        self._on_disconnect: DisconnectHandler | None = None
        self._event_stream_supported: bool | None = None

    async def start(self, on_message: MessageHandler, on_disconnect: DisconnectHandler) -> None:
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._closing = False
        self._event_stream_supported = None
        connect_timeout = httpx.Timeout(
            self.request_timeout_s,
            connect=self.connect_timeout_s,
            read=None,
        )
        self.client = httpx.AsyncClient(
            timeout=connect_timeout,
            headers=self.headers,
            follow_redirects=True,
        )
        self.alive = True

    def _headers(self, *, event_stream: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "text/event-stream" if event_stream else "application/json, text/event-stream",
        }
        if not event_stream:
            headers["Content-Type"] = "application/json"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _remember_session(self, response: httpx.Response) -> None:
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id
        protocol_version = response.headers.get("MCP-Protocol-Version")
        if protocol_version:
            self.protocol_version = protocol_version

    async def send(self, message: JSONRPCMessage) -> None:
        if not self.alive or self.client is None:
            raise MCPTransportError("MCP HTTP transport is not connected")
        timeout = httpx.Timeout(self.request_timeout_s, connect=self.connect_timeout_s)
        try:
            async with self.client.stream(
                "POST",
                self.url,
                json=message,
                headers=self._headers(),
                timeout=timeout,
            ) as response:
                self._remember_session(response)
                if response.status_code < 200 or response.status_code >= 300:
                    raise MCPTransportError(
                        f"MCP HTTP endpoint returned status {response.status_code}"
                    )
                if response.status_code in (202, 204):
                    return
                content_type = response.headers.get("content-type", "").lower()
                if "text/event-stream" in content_type:
                    await self._consume_sse(response)
                    return
                body = await response.aread()
                if not body.strip():
                    return
                try:
                    incoming = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError) as err:
                    raise MCPTransportError("MCP HTTP endpoint returned invalid JSON") from err
                self._deliver(incoming)
        except MCPTransportError:
            raise
        except (httpx.TimeoutException, httpx.RequestError) as err:
            raise MCPTransportError(f"MCP HTTP request failed: {err}") from err

    async def _consume_sse(self, response: httpx.Response) -> None:
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if line == "":
                if data_lines:
                    self._deliver_sse_data(data_lines)
                    data_lines = []
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
        if data_lines:
            self._deliver_sse_data(data_lines)

    def _deliver_sse_data(self, data_lines: list[str]) -> None:
        try:
            incoming = json.loads("\n".join(data_lines))
        except json.JSONDecodeError as err:
            logger.warning("Ignoring invalid JSON data in MCP SSE stream: %s", err)
            return
        self._deliver(incoming)

    def _deliver(self, incoming: Any) -> None:
        if isinstance(incoming, list):
            for item in incoming:
                self._deliver(item)
        elif isinstance(incoming, dict) and self._on_message is not None:
            self._on_message(incoming)

    async def open_event_stream(self) -> None:
        """Opens the optional server-to-client SSE notification stream after initialize."""
        if (
            not self.alive
            or self.client is None
            or not self.session_id
            or self._event_stream_supported is False
            or (self._event_task is not None and not self._event_task.done())
        ):
            return
        self._event_task = asyncio.create_task(self._event_loop(), name="mcp-http-events")

    async def _event_loop(self) -> None:
        if self.client is None:
            return
        while self.alive and not self._closing:
            try:
                async with self.client.stream(
                    "GET",
                    self.url,
                    headers=self._headers(event_stream=True),
                    timeout=httpx.Timeout(self.request_timeout_s, connect=self.connect_timeout_s, read=None),
                ) as response:
                    self._remember_session(response)
                    if response.status_code in (405, 406):
                        # A valid server may opt out of GET-initiated event streams.
                        self._event_stream_supported = False
                        return
                    if response.status_code < 200 or response.status_code >= 300:
                        raise MCPTransportError(
                            f"MCP HTTP event stream returned status {response.status_code}"
                        )
                    if "text/event-stream" not in response.headers.get("content-type", "").lower():
                        self._event_stream_supported = False
                        return
                    self._event_stream_supported = True
                    await self._consume_sse(response)
                if self.alive and not self._closing:
                    # GET streams may end normally; reopen them without discarding the MCP session.
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                return
            except Exception as err:
                if self._closing:
                    return
                failure = err if isinstance(err, MCPTransportError) else MCPTransportError(
                    f"MCP HTTP event stream failed: {err}"
                )
                self.alive = False
                if self._on_disconnect is not None:
                    await self._on_disconnect(failure)
                return

    async def close(self) -> None:
        self._closing = True
        self.alive = False
        if self._event_task is not None and not self._event_task.done():
            self._event_task.cancel()
            await asyncio.gather(self._event_task, return_exceptions=True)
        self._event_task = None
        client, self.client = self.client, None
        if client is not None:
            await client.aclose()

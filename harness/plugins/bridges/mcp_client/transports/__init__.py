"""Transport interfaces for the MCP client bridge."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

JSONRPCMessage = dict[str, Any]
MessageHandler = Callable[[JSONRPCMessage], None]
DisconnectHandler = Callable[[Exception], Awaitable[None]]


class MCPTransportError(ConnectionError):
    """A transport failed or the peer invalidated its current MCP session."""


class Transport(Protocol):
    """Bidirectional transport used by the transport-agnostic JSON-RPC client."""

    name: str
    alive: bool

    async def start(self, on_message: MessageHandler, on_disconnect: DisconnectHandler) -> None:
        """Opens the underlying connection and begins delivering incoming messages."""
        ...

    async def send(self, message: JSONRPCMessage) -> None:
        """Sends one JSON-RPC request or notification."""
        ...

    async def open_event_stream(self) -> None:
        """Starts optional server-initiated messages after MCP initialization."""
        ...

    async def close(self) -> None:
        """Closes all transport resources."""
        ...


__all__ = [
    "DisconnectHandler",
    "JSONRPCMessage",
    "MCPTransportError",
    "MessageHandler",
    "Transport",
]

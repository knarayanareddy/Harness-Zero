"""Newline-delimited JSON-RPC transport over a child process' stdio streams."""
from __future__ import annotations

import asyncio
import json
import logging

from . import DisconnectHandler, JSONRPCMessage, MCPTransportError, MessageHandler

logger = logging.getLogger(__name__)


class StdioTransport:
    """Owns the MCP subprocess while leaving request correlation to ``MCPClient``."""

    name = "stdio"

    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.process: asyncio.subprocess.Process | None = None
        self.alive = False
        self._closing = False
        self._reader_task: asyncio.Task[None] | None = None
        self._on_message: MessageHandler | None = None
        self._on_disconnect: DisconnectHandler | None = None

    async def start(self, on_message: MessageHandler, on_disconnect: DisconnectHandler) -> None:
        if not self.command:
            raise ValueError("stdio MCP transport requires a command")
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._closing = False
        self.process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        self.alive = True
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"mcp-stdio-{self.process.pid}")

    async def send(self, message: JSONRPCMessage) -> None:
        process = self.process
        if not self.alive or process is None or process.stdin is None or process.returncode is not None:
            raise MCPTransportError("MCP stdio server process is not running")
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            process.stdin.write(payload)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as err:
            raise MCPTransportError(f"MCP stdio write failed: {err}") from err

    async def _read_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        failure: Exception = MCPTransportError("MCP stdio server closed the connection")
        try:
            while not self._closing:
                line = await process.stdout.readline()
                if not line:
                    if process.returncode is None:
                        await process.wait()
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError as err:
                    logger.warning("Ignoring malformed JSON-RPC line from MCP stdio server: %s", err)
                    continue
                if isinstance(message, dict) and self._on_message is not None:
                    self._on_message(message)
        except asyncio.CancelledError:
            return
        except Exception as err:  # a malformed peer/closed pipe is a transport failure
            failure = MCPTransportError(f"MCP stdio reader failed: {err}")
        finally:
            was_alive = self.alive
            self.alive = False
            if was_alive and not self._closing and self._on_disconnect is not None:
                await self._on_disconnect(failure)

    async def open_event_stream(self) -> None:
        """Stdio already has a bidirectional stream; there is no separate GET stream."""

    async def close(self) -> None:
        self._closing = True
        self.alive = False
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        process = self.process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=2.0)
        except Exception:
            try:
                if process.returncode is None:
                    process.kill()
                await process.wait()
            except Exception:
                pass
        self.process = None

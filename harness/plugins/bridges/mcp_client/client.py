"""
MCPClient: Stdio JSON-RPC 2.0 client for Model Context Protocol servers (§10, BRG-02, SIM-01).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from itertools import count
from typing import Any

logger = logging.getLogger(__name__)

# Patterns for sensitive environment variables to scrub
_SECRET_PATTERNS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "AUTH",
    "PRIVATE",
)


def _scrub_environment(custom_env: dict[str, str] | None = None) -> dict[str, str]:
    """Provides a safe sanitized environment merged with explicit server env."""
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        k_upper = k.upper()
        if any(pat in k_upper for pat in _SECRET_PATTERNS):
            continue
        if k_upper.startswith("AWS_") or k_upper in ("GH_TOKEN", "GITHUB_TOKEN"):
            continue
        env[k] = v

    env["CI"] = "1"
    env["HARNESS_AGENT"] = "1"
    env["PAGER"] = "cat"
    env["TERM"] = "dumb"

    if custom_env:
        env.update(custom_env)
    return env


class MCPError(Exception):
    """Raised when an MCP JSON-RPC call returns an error object."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"MCP Error [{code}]: {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPClient:
    """
    Manages child process connection, stdio JSON-RPC protocol, and lifecycle.
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        restart_on_crash: bool = True,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.custom_env = env or {}
        self.restart_on_crash = restart_on_crash

        self.status: str = "stopped"  # "connected" | "error" | "stopped"
        self.last_error: str | None = None

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._id_counter = count(1)
        self._pending_requests: dict[int, asyncio.Future[Any]] = {}
        self._explicitly_stopped = False

    async def start(self) -> None:
        """Spawns server subprocess and performs MCP initialize handshake."""
        self._explicitly_stopped = False
        clean_env = _scrub_environment(self.custom_env)

        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=clean_env,
            )
            self._reader_task = asyncio.create_task(self._read_loop())

            # Perform handshake
            init_res = await self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {},
                        "resources": {},
                        "prompts": {},
                    },
                    "clientInfo": {
                        "name": "harness-zero",
                        "version": "2.0.0",
                    },
                },
                timeout=15.0,
            )
            logger.info(f"MCP server '{self.name}' initialized: {init_res.get('serverInfo', {})}")

            await self.notify("notifications/initialized", {})
            self.status = "connected"
            self.last_error = None
        except Exception as err:
            self.status = "error"
            self.last_error = str(err)
            logger.error(f"Failed to start MCP server '{self.name}': {err}")
            await self.stop()
            raise

    async def _read_loop(self) -> None:
        """Reads newline-delimited JSON-RPC responses from server stdout."""
        if not self._proc or not self._proc.stdout:
            return

        while not self._explicitly_stopped:
            try:
                line = await self._proc.stdout.readline()
                if not line:
                    break

                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                try:
                    msg = json.loads(text)
                except Exception as json_err:
                    logger.warning(f"Malformed JSON from MCP server '{self.name}': {json_err}")
                    continue

                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        if "error" in msg and msg["error"]:
                            err_obj = msg["error"]
                            fut.set_exception(
                                MCPError(
                                    code=err_obj.get("code", -1),
                                    message=err_obj.get("message", "Unknown error"),
                                    data=err_obj.get("data"),
                                )
                            )
                        else:
                            fut.set_result(msg.get("result", {}))
            except Exception as err:
                if not self._explicitly_stopped:
                    logger.warning(f"Error in MCP client '{self.name}' read loop: {err}")
                break

        # Process exited or EOF reached
        if not self._explicitly_stopped:
            self.status = "error"
            self.last_error = "Server process closed connection unexpectedly"
            # Cancel all pending requests
            for fut in self._pending_requests.values():
                if not fut.done():
                    fut.set_exception(ConnectionResetError(self.last_error))
            self._pending_requests.clear()

    async def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
        """Sends a JSON-RPC request and awaits result."""
        if self.status != "connected" and method != "initialize":
            raise ConnectionError(f"MCP server '{self.name}' is not connected (status: {self.status})")

        if not self._proc or not self._proc.stdin or self._proc.returncode is not None:
            self.status = "error"
            raise ConnectionResetError(f"MCP server '{self.name}' process is not running")

        req_id = next(self._id_counter)
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending_requests[req_id] = fut

        payload = json.dumps(msg).encode("utf-8") + b"\n"
        try:
            self._proc.stdin.write(payload)
            await self._proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception as err:
            self._pending_requests.pop(req_id, None)
            raise err

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Sends a JSON-RPC notification (no response expected)."""
        if not self._proc or not self._proc.stdin or self._proc.returncode is not None:
            return

        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        payload = json.dumps(msg).encode("utf-8") + b"\n"
        try:
            self._proc.stdin.write(payload)
            await self._proc.stdin.drain()
        except Exception as err:
            logger.warning(f"Failed to send notification to MCP server '{self.name}': {err}")

    async def list_tools(self) -> list[dict[str, Any]]:
        """Queries server for supported tools."""
        res = await self.request("tools/list")
        return res.get("tools", [])

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Calls an MCP tool by name with arguments."""
        return await self.request("tools/call", {"name": name, "arguments": arguments or {}})

    async def list_resources(self) -> list[dict[str, Any]]:
        """Queries server for available resources."""
        res = await self.request("resources/list")
        return res.get("resources", [])

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Reads content of an MCP resource by URI."""
        return await self.request("resources/read", {"uri": uri})

    async def list_prompts(self) -> list[dict[str, Any]]:
        """Queries server for available prompts."""
        res = await self.request("prompts/list")
        return res.get("prompts", [])

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Retrieves messages for a prompt template."""
        return await self.request("prompts/get", {"name": name, "arguments": arguments or {}})

    async def stop(self) -> None:
        """Gracefully terminates server process."""
        self._explicitly_stopped = True
        self.status = "stopped"

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()

        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                    await self._proc.wait()
                except Exception:
                    pass
            self._proc = None

    def get_status(self) -> dict[str, Any]:
        """Reports server connection state and diagnostic status."""
        return {
            "name": self.name,
            "status": self.status,
            "last_error": self.last_error,
            "pid": self._proc.pid if self._proc and self._proc.returncode is None else None,
        }

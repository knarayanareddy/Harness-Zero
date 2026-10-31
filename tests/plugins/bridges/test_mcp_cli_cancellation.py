"""Cancellation through the shipped MCP stdio process and real kernel gate."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from harness.plugins.bridges.mcp_server.server import MCPServer

REPO_ROOT = Path(__file__).resolve().parents[3]


@asynccontextmanager
async def _mcp_cli(workspace: Path, config: Path):
    env = os.environ.copy()
    env["HARNESS_CONFIG_DIR"] = str(config)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "harness.cli",
        "mcp",
        "--workspace",
        str(workspace),
        cwd=str(REPO_ROOT),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_task = asyncio.create_task(process.stderr.read())
    try:
        yield process
    finally:
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
            await process.stdin.wait_closed()
        await asyncio.wait_for(process.wait(), timeout=8)
        stderr = (await stderr_task).decode("utf-8", errors="replace")
        assert process.returncode == 0, stderr


async def _send(process: asyncio.subprocess.Process, message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
    await asyncio.wait_for(process.stdin.drain(), timeout=2)


async def _receive(process: asyncio.subprocess.Process, timeout: float = 5.0) -> dict[str, Any]:
    assert process.stdout is not None
    line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
    assert line, "MCP CLI exited before returning a JSON-RPC response"
    return json.loads(line)


async def _wait_for_file(path: Path, timeout: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path.is_file():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Child readiness marker was not created: {path.name}")


def _owned_process_running(pid: int, token: str) -> bool:
    result = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        timeout=1,
        check=False,
    )
    return token in result.stdout


@pytest.mark.asyncio
async def test_cli_cancels_active_gate_call_and_ignores_bad_or_late_cancel_ids(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps(
            {
                "kernel": {"security": {"preset": "open"}},
                "plugins": {"bridge-mcp-server": {"expose": ["run_command", "view_file"]}},
            }
        ),
        encoding="utf-8",
    )

    token = f"mcp_t18b_{uuid.uuid4().hex}"
    script = "\n".join(
        [
            "import json, os, pathlib, signal, sys, time",
            "token=sys.argv[1]",
            "def stop(_sig, _frame):",
            "    pathlib.Path('child-stopped.txt').write_text(token)",
            "    raise SystemExit(0)",
            "signal.signal(signal.SIGTERM, stop)",
            "pathlib.Path('child-ready.json.tmp').write_text(json.dumps({'pid':os.getpid(),'parent':os.getppid(),'token':token}))",
            "pathlib.Path('child-ready.json.tmp').replace('child-ready.json')",
            "time.sleep(0.2)",
            "pathlib.Path('survived-invalid-cancel.txt').write_text(token)",
            "time.sleep(3)",
            "pathlib.Path('late-marker.txt').write_text(token)",
        ]
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} {shlex.quote(token)}"

    async with _mcp_cli(workspace, config) as process:
        await _send(process, {"jsonrpc": "2.0", "id": "init", "method": "initialize"})
        initialized = await _receive(process)
        assert initialized["id"] == "init"

        await _send(process, {"jsonrpc": "2.0", "id": "list", "method": "tools/list"})
        listed = await _receive(process)
        assert listed["id"] == "list"
        assert {tool["name"] for tool in listed["result"]["tools"]} == {"run_command", "view_file"}

        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 73,
                "method": "tools/call",
                "params": {
                    "name": "run_command",
                    "arguments": {"command": command, "cwd": ".", "wait_ms_before_async": 10000},
                },
            },
        )
        ready_path = workspace / "child-ready.json"
        await _wait_for_file(ready_path, timeout=10)
        child = json.loads(ready_path.read_text(encoding="utf-8"))
        assert child["token"] == token

        await _send(process, {"jsonrpc": "2.0", "id": "active-list", "method": "tools/list"})
        active_list = await _receive(process, timeout=1)
        assert active_list["id"] == "active-list"
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": "busy-call",
                "method": "tools/call",
                "params": {"name": "view_file", "arguments": {"file_path": "child-ready.json"}},
            },
        )
        busy = await _receive(process, timeout=1)
        assert busy["id"] == "busy-call"
        assert busy["error"]["code"] == -32000

        # bool, string, and differently typed numeric IDs must not alias the active id.
        for cancel_id in (True, "73", 73.0, 999):
            await _send(
                process,
                {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": cancel_id}},
            )
        await _send(process, {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}})
        await _wait_for_file(workspace / "survived-invalid-cancel.txt", timeout=2)

        cancel_started = asyncio.get_running_loop().time()
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 73, "reason": "test cancellation"},
            },
        )
        await _wait_for_file(workspace / "child-stopped.txt", timeout=2)

        # A repeated late cancellation is ignored; the normal read proves the call lock
        # was released only after the real Gate/ToolContext child cleanup completed.
        cancel_deadline = cancel_started + 2
        attempt = 0
        while True:
            request_id = 7 + attempt
            await _send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": "view_file", "arguments": {"file_path": "survived-invalid-cancel.txt"}},
                },
            )
            remaining = cancel_deadline - asyncio.get_running_loop().time()
            assert remaining > 0, "Cancelled tool cleanup exceeded the 2-second bound"
            response = await _receive(process, timeout=remaining)
            assert type(response.get("id")) is int and response["id"] == request_id
            if response.get("error", {}).get("code") == -32000:
                attempt += 1
                continue
            break
        assert response["result"]["isError"] is False
        assert token in response["result"]["content"][0]["text"]
        assert not (workspace / "late-marker.txt").exists()
        assert not _owned_process_running(child["pid"], token)
        assert not _owned_process_running(child["parent"], token)
        elapsed = asyncio.get_running_loop().time() - cancel_started
        assert elapsed < 2

        # Cancellations for completed call/init IDs must not interfere with a later call.
        for completed_id in (73, "init"):
            await _send(
                process,
                {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": completed_id}},
            )
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 100_000,
                "method": "tools/call",
                "params": {"name": "view_file", "arguments": {"file_path": "child-ready.json"}},
            },
        )
        late_control = await _receive(process, timeout=2)
        assert type(late_control["id"]) is int and late_control["id"] == 100_000
        assert late_control["result"]["isError"] is False


@pytest.mark.asyncio
async def test_cli_half_close_preserves_short_read_response(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    (workspace / "visible.txt").write_text("half-close read control")
    (config / "settings.json").write_text(json.dumps({
        "plugins": {"bridge-mcp-server": {"expose": ["view_file"]}},
    }))
    async with _mcp_cli(workspace, config) as process:
        await _send(process, {"jsonrpc": "2.0", "id": "init", "method": "initialize"})
        assert (await _receive(process))["id"] == "init"
        await _send(process, {"jsonrpc": "2.0", "id": "read", "method": "tools/call",
            "params": {"name": "view_file", "arguments": {"file_path": "visible.txt"}}})
        process.stdin.close()
        await process.stdin.wait_closed()
        response = await _receive(process, timeout=2)
        assert response["id"] == "read"
        assert response["result"]["isError"] is False
        assert "half-close read control" in str(response["result"]["content"])


@pytest.mark.asyncio
async def test_cli_stdin_eof_cancels_owned_active_command(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps(
            {
                "kernel": {"security": {"preset": "open"}},
                "plugins": {"bridge-mcp-server": {"expose": ["run_command"]}},
            }
        ),
        encoding="utf-8",
    )

    token = f"mcp_t18b_eof_{uuid.uuid4().hex}"
    script = "\n".join(
        [
            "import json, os, pathlib, signal, sys, time",
            "token=sys.argv[1]",
            "def stop(_sig, _frame):",
            "    pathlib.Path('eof-stopped.txt').write_text(token)",
            "    raise SystemExit(0)",
            "signal.signal(signal.SIGTERM, stop)",
            "pathlib.Path('eof-ready.json.tmp').write_text(json.dumps({'pid':os.getpid(),'parent':os.getppid(),'token':token}))",
            "pathlib.Path('eof-ready.json.tmp').replace('eof-ready.json')",
            "time.sleep(3)",
            "pathlib.Path('eof-late-marker.txt').write_text(token)",
        ]
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} {shlex.quote(token)}"

    async with _mcp_cli(workspace, config) as process:
        await _send(process, {"jsonrpc": "2.0", "id": "init", "method": "initialize"})
        assert (await _receive(process))["id"] == "init"
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": "eof-call",
                "method": "tools/call",
                "params": {
                    "name": "run_command",
                    "arguments": {"command": command, "cwd": ".", "wait_ms_before_async": 10000},
                },
            },
        )
        ready_path = workspace / "eof-ready.json"
        await _wait_for_file(ready_path, timeout=10)
        child = json.loads(ready_path.read_text(encoding="utf-8"))
        assert child["token"] == token

        assert process.stdin is not None
        process.stdin.close()
        await process.stdin.wait_closed()
        shutdown_started = asyncio.get_running_loop().time()
        await asyncio.wait_for(process.wait(), timeout=2)
        assert asyncio.get_running_loop().time() - shutdown_started < 2

        assert (workspace / "eof-stopped.txt").read_text(encoding="utf-8") == token
        assert not (workspace / "eof-late-marker.txt").exists()
        assert not _owned_process_running(child["pid"], token)
        assert not _owned_process_running(child["parent"], token)


@pytest.mark.asyncio
async def test_cli_rejects_reused_request_id_before_late_cancel_can_match(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    (workspace / "control.txt").write_text("fresh call works", encoding="utf-8")
    (config / "settings.json").write_text(
        json.dumps(
            {
                "kernel": {"security": {"preset": "open"}},
                "plugins": {"bridge-mcp-server": {"expose": ["run_command", "view_file"]}},
            }
        ),
        encoding="utf-8",
    )

    token = f"mcp_t18b_reuse_{uuid.uuid4().hex}"
    marker = workspace / "reused-call-started.txt"
    script = "import pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text('started'); time.sleep(3)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} {shlex.quote(str(marker))}"

    async with _mcp_cli(workspace, config) as process:
        await _send(process, {"jsonrpc": "2.0", "id": "init", "method": "initialize"})
        assert (await _receive(process))["id"] == "init"

        for request_id in (42, "42"):
            await _send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": "view_file", "arguments": {"file_path": "control.txt"}},
                },
            )
            typed_response = await _receive(process)
            assert typed_response["id"] == request_id
            assert typed_response["result"]["isError"] is False

        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": token,
                "method": "tools/call",
                "params": {"name": "view_file", "arguments": {"file_path": "control.txt"}},
            },
        )
        completed = await _receive(process)
        assert completed["id"] == token
        assert completed["result"]["content"][0]["text"].endswith("fresh call works")

        # Reusing a completed ID must not start a tool or let this delayed cancel
        # target a new call that happens to use the same ID.
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": token,
                "method": "tools/call",
                "params": {"name": "run_command", "arguments": {"command": command, "cwd": "."}},
            },
        )
        await _send(
            process,
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": token}},
        )
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": "fresh-after-reuse",
                "method": "tools/call",
                "params": {"name": "view_file", "arguments": {"file_path": "control.txt"}},
            },
        )

        duplicate = await _receive(process, timeout=2)
        assert duplicate["id"] == token
        assert duplicate["error"]["code"] == -32600
        fresh = await _receive(process, timeout=2)
        assert fresh["id"] == "fresh-after-reuse"
        assert fresh["result"]["isError"] is False
        assert fresh["result"]["content"][0]["text"].endswith("fresh call works")
        assert not marker.exists(), "reused request ID unexpectedly started its tool handler"


@pytest.mark.asyncio
async def test_stdio_request_id_budget_stops_active_work_with_errors(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(MCPServer, "_MAX_REQUEST_IDS_PER_CONNECTION", 1)
    reader = asyncio.StreamReader()
    reader.feed_data(
        b'{"jsonrpc":"2.0","id":"active","method":"tools/call"}\n'
        b'{"jsonrpc":"2.0","id":"over-budget","method":"tools/list"}\n'
    )
    reader.feed_eof()

    class MemoryWriter:
        def __init__(self):
            self.data = bytearray()

        def write(self, data: bytes) -> None:
            self.data.extend(data)

        async def drain(self) -> None:
            return None

    writer = MemoryWriter()
    call_started = asyncio.Event()

    async def executor(_call, _cancel_event):
        raise AssertionError("The mocked handler must own this test's active work")

    server = MCPServer(lambda: [], executor, cancellable_tool_executor=executor)

    async def active_call(req, *, cancel_event=None):
        call_started.set()
        assert cancel_event is not None
        await cancel_event.wait()
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"content": []}}

    server.handle_request = active_call
    await server.run_stdio(reader, writer)

    responses = [json.loads(line) for line in writer.data.decode("utf-8").splitlines()]
    assert call_started.is_set()
    assert [response["id"] for response in responses] == ["active", "over-budget"]
    assert all(response["error"]["code"] == -32000 for response in responses)
    assert "result" not in responses[0], "active work must not be reported as successful on exhaustion"


@pytest.mark.asyncio
async def test_stdio_eof_bounds_active_response_flush_within_grace():
    eof_observed = asyncio.Event()

    class EOFReader(asyncio.StreamReader):
        async def readline(self):
            line = await super().readline()
            if not line:
                eof_observed.set()
            return line

    reader = EOFReader()
    reader.feed_data(b'{"jsonrpc":"2.0","id":"active","method":"tools/call"}\n')
    call_started = asyncio.Event()
    finish_call = asyncio.Event()

    class BlockingWriter:
        def __init__(self):
            self.data = bytearray()
            self.drain_started = asyncio.Event()
            self.aborted = asyncio.Event()
            self.transport = self

        def write(self, data: bytes) -> None:
            self.data.extend(data)

        async def drain(self) -> None:
            self.drain_started.set()
            await asyncio.Event().wait()

        def abort(self) -> None:
            self.aborted.set()

    writer = BlockingWriter()
    server = MCPServer(lambda: [], lambda _call: asyncio.sleep(0))

    async def active_call(req, *, cancel_event=None):
        call_started.set()
        await finish_call.wait()
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"content": []}}

    server.handle_request = active_call
    serve_task = asyncio.create_task(server.run_stdio(reader, writer))
    await asyncio.wait_for(call_started.wait(), timeout=0.2)
    eof_started = asyncio.get_running_loop().time()
    reader.feed_eof()
    await asyncio.wait_for(eof_observed.wait(), timeout=0.2)
    finish_call.set()

    await asyncio.wait_for(serve_task, timeout=0.5)
    assert asyncio.get_running_loop().time() - eof_started < 0.25
    assert writer.drain_started.is_set()
    assert writer.aborted.is_set(), "blocked response drain must abort output at the EOF deadline"
    assert b'"id": "active"' in writer.data


@pytest.mark.asyncio
async def test_stdio_inline_flush_cannot_block_eof_dispatch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(MCPServer, "_STDIO_WRITE_DRAIN_TIMEOUT_S", 0.03, raising=False)
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"jsonrpc":"2.0","id":"init","method":"initialize"}\n')

    class BlockingWriter:
        def __init__(self):
            self.data = bytearray()
            self.drain_started = asyncio.Event()
            self.aborted = asyncio.Event()
            self.transport = self

        def write(self, data: bytes) -> None:
            self.data.extend(data)

        async def drain(self) -> None:
            self.drain_started.set()
            await asyncio.Event().wait()

        def abort(self) -> None:
            self.aborted.set()

    writer = BlockingWriter()
    server = MCPServer(lambda: [], lambda _call: asyncio.sleep(0))
    serve_task = asyncio.create_task(server.run_stdio(reader, writer))
    await asyncio.wait_for(writer.drain_started.wait(), timeout=0.2)
    reader.feed_eof()

    await asyncio.wait_for(serve_task, timeout=0.2)
    assert writer.aborted.is_set(), "stalled inline response must abort instead of hiding EOF forever"
    assert b'"id": "init"' in writer.data

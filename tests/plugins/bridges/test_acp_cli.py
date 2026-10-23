"""CLI entrypoint binds ACP to a fresh real kernel runtime."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from harness.cli import main
from harness.kernel.bootstrap import bootstrap_kernel
from harness.plugins.bridges.acp import ACPServer
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ModelInfo, TextDelta


@pytest.mark.asyncio
async def test_acp_cli_creates_lists_and_runs_only_connection_owned_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """In-process bootstrap/Commands control; actual process wire coverage is below."""
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    workspace.mkdir()
    config.mkdir()

    # A persisted session must not become the ACP connection's implicit session.
    previous = await bootstrap_kernel(workspace=workspace, config_dir=config, resume_session=False)
    old_session = await previous.commands.sessions_create(metadata={"title": "existing"})
    await previous.registry.unload_all()

    async def serve(commands, bus):
        assert commands.session_manager.active_session is None
        commands.registry.register_provider(
            ScriptedProvider(
                responses=[[TextDelta(text="CLI offline result"), Done(stop_reason="end_turn")]],
                models=[ModelInfo(id="cli-script", display_name="CLI Script")],
                strict_exhaustion=True,
            ),
            plugin_name="acp_cli_scripted",
        )
        server = ACPServer(commands=commands, bus=bus)
        listing = await server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "session/list"})
        assert listing["result"] == []

        created = await server.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"title": "new ACP session"}
        })
        session_id = created["result"]["session_id"]
        assert session_id != old_session["session_id"]
        listing = await server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "session/list"})
        assert [row["session_id"] for row in listing["result"]] == [session_id]

        turn = await server.handle_request({
            "jsonrpc": "2.0", "id": 4, "method": "prompt",
            "params": {
                "session_id": session_id,
                "prompt": "offline CLI turn",
                "options": {"provider": "scripted", "model": "cli-script"},
            },
        })
        assert turn["result"]["status"] == "completed"
        assert turn["result"]["output"] == "CLI offline result"

    monkeypatch.setattr("harness.plugins.bridges.acp.server.run_acp_server_from_stdio", serve)
    result = await asyncio.to_thread(
        CliRunner().invoke, main, ["acp", "--workspace", str(workspace)]
    )
    assert result.exit_code == 0, result.output


class ACPProcess:
    """Small real-stdio peer for the public `python -m harness.cli acp` entrypoint."""

    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self.seen: list[dict] = []
        assert process.stderr is not None
        self.stderr_task = asyncio.create_task(process.stderr.read())

    async def send(self, request: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        await self.process.stdin.drain()

    async def receive(self) -> dict:
        assert self.process.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(self.process.stdout.readline(), timeout=2.0)
            except TimeoutError as exc:
                raise AssertionError(f"timed out waiting for ACP output; seen={self.seen!r}") from exc
            assert line, f"ACP process exited before a response; observed={self.seen!r}"
            message = json.loads(line)
            self.seen.append(message)
            return message

    async def response(self, request_id: int) -> dict:
        while True:
            message = await self.receive()
            if message.get("id") == request_id:
                return message

    async def notification(self, method: str) -> dict:
        while True:
            message = await self.receive()
            if message.get("method") == method:
                return message

    async def close(self) -> None:
        # Drain both pipes while waiting: Process.wait() alone can deadlock on
        # an unread PIPE even after the child has otherwise finished its work.
        async with asyncio.timeout(2.0):
            assert self.process.stdin is not None and self.process.stdout is not None
            if not self.process.stdin.is_closing():
                self.process.stdin.close()
                await self.process.stdin.wait_closed()
            await asyncio.gather(self.process.wait(), self.process.stdout.read(), self.stderr_task)
        assert self.process.returncode == 0, self.stderr_task.result().decode(errors="replace")[-4096:]


@pytest.mark.asyncio
async def test_stdio_peer_drains_stderr_before_waiting_for_stdout():
    script = (
        "import sys; sys.stderr.write('x'*524288); sys.stderr.flush(); "
        "sys.stdout.write('{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\\n'); "
        "sys.stdout.flush(); sys.stdin.buffer.read()"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    peer = ACPProcess(process)
    try:
        assert (await peer.response(1))["result"] == {}
        await peer.close()
        assert process.returncode == 0
    finally:
        if process.returncode is None:
            process.kill()  # Only the exact child handle this test created.
        stderr_task = getattr(peer, "stderr_task", None)
        if stderr_task is None:
            stderr_task = asyncio.create_task(process.stderr.read())
        await asyncio.gather(process.wait(), process.stdout.read(), stderr_task)


@pytest.mark.asyncio
async def test_real_acp_cli_stdio_create_list_prompt_cancel_and_permission(tmp_path: Path):
    root = Path(__file__).resolve().parents[3]
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    config.mkdir()
    script = tmp_path / "mock-script.json"
    # This control asserts foreground completion. Avoid background-job notices
    # consuming a later scripted turn; the peer's two-second reply bound is unchanged.
    script.write_text(json.dumps([
        {"text": "offline subprocess output", "stop_reason": "end_turn"},
        {
            "tool_calls": [{
                "id": "cli-approved-call",
                "name": "run_command",
                "arguments": {"command": "touch approved-by-acp.txt", "cwd": ".", "wait_ms_before_async": 10000},
            }],
            "stop_reason": "tool_use",
        },
        {"text": "approved command completed", "stop_reason": "end_turn"},
        {
            "tool_calls": [{
                "id": "cli-cancelled-call",
                "name": "run_command",
                "arguments": {"command": "touch cancelled-by-acp.txt", "cwd": ".", "wait_ms_before_async": 0},
            }],
            "stop_reason": "tool_use",
        },
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")

    # A persisted ID must remain foreign even though this CLI boot intentionally does not resume it.
    previous = await bootstrap_kernel(workspace=workspace, config_dir=config, resume_session=False)
    persisted = await previous.commands.sessions_create(session_id="session_aaaaaaaaaaaa")
    await previous.registry.unload_all()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["HARNESS_CONFIG_DIR"] = str(config)

    async def launch() -> ACPProcess:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "harness.cli", "acp", "--workspace", str(workspace),
            cwd=root,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return ACPProcess(process)

    first, second = await launch(), None
    try:
        await first.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        initialized = await first.response(1)
        assert initialized["result"]["capabilities"]["questions"] is True
        await first.send({"jsonrpc": "2.0", "id": 2, "method": "session/list"})
        assert (await first.response(2))["result"] == []

        await first.send({
            "jsonrpc": "2.0", "id": 3, "method": "session/new",
            "params": {"session_id": persisted["session_id"]},
        })
        duplicate = await first.response(3)
        assert duplicate["error"]["code"] == -32001
        await first.send({
            "jsonrpc": "2.0", "id": 4, "method": "session/new",
            "params": {"session_id": "session_bbbbbbbbbbbb"},
        })
        first_session = (await first.response(4))["result"]["session_id"]

        await first.send({
            "jsonrpc": "2.0", "id": 5, "method": "prompt",
            "params": {"session_id": first_session, "prompt": "offline prompt", "options": {"provider": "mock", "model": "mock-v1"}},
        })
        offline = await first.response(5)
        assert offline["result"]["status"] == "completed"
        assert offline["result"]["output"] == "offline subprocess output"

        await first.send({
            "jsonrpc": "2.0", "id": 6, "method": "prompt",
            "params": {"session_id": first_session, "prompt": "request approval", "options": {"provider": "mock", "model": "mock-v1"}},
        })
        approval = await first.notification("permission/request")
        approval_id = approval["params"]["request_id"]

        second = await launch()
        await second.send({
            "jsonrpc": "2.0", "id": 1, "method": "session/new",
            "params": {"session_id": first_session},
        })
        assert (await second.response(1))["error"]["code"] == -32001
        await second.send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"session_id": "session_cccccccccccc"}})
        second_session = (await second.response(2))["result"]["session_id"]
        await second.send({"jsonrpc": "2.0", "id": 3, "method": "session/list"})
        assert [row["session_id"] for row in (await second.response(3))["result"]] == [second_session]
        await second.send({
            "jsonrpc": "2.0", "id": 4, "method": "permission/resolve",
            "params": {"request_id": approval_id, "approved": True},
        })
        assert (await second.response(4))["error"]["code"] == -32001
        await second.send({
            "jsonrpc": "2.0", "id": 5, "method": "prompt",
            "params": {"session_id": first_session, "prompt": "foreign", "options": {"provider": "mock", "model": "mock-v1"}},
        })
        assert (await second.response(5))["error"]["code"] == -32001

        await first.send({
            "jsonrpc": "2.0", "id": 7, "method": "permission/resolve",
            "params": {"request_id": approval_id, "approved": True},
        })
        assert (await first.response(7))["result"]["status"] == "resolved"
        approved = await first.response(6)
        assert approved["result"]["status"] == "completed"
        assert "approved command completed" in approved["result"]["output"]
        assert (workspace / "approved-by-acp.txt").exists()

        await first.send({
            "jsonrpc": "2.0", "id": 8, "method": "prompt",
            "params": {"session_id": first_session, "prompt": "request then cancel", "options": {"provider": "mock", "model": "mock-v1"}},
        })
        pending = await first.notification("permission/request")
        assert pending["params"]["session_id"] == first_session
        await first.send({"jsonrpc": "2.0", "id": 9, "method": "cancel", "params": {"session_id": first_session}})
        assert (await first.response(9))["result"]["status"] == "cancellation_requested"
        cancelled = await first.response(8)
        assert cancelled["result"]["status"] == "cancelled"
        assert not (workspace / "cancelled-by-acp.txt").exists()
    finally:
        try:
            await first.close()
        finally:
            if second is not None:
                await second.close()

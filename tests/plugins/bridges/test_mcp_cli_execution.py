"""Actual stdio CLI mediation through the kernel gate, not a replacement executor."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


async def _stop_process(process):
    if process.stdin is not None and not process.stdin.is_closing():
        process.stdin.close()
        try:
            await asyncio.wait_for(process.stdin.wait_closed(), 5)
        except (BrokenPipeError, ConnectionResetError):
            pass
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), 10)
    except TimeoutError:
        timed_out = True
        process.kill()
        await asyncio.wait_for(process.wait(), 5)
    return int(process.returncode or 0), timed_out


async def _run_mcp_cli(tmp_path, settings, requests, *, secrets=None, reply_timeouts=None, workspace_files=None):
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    (config / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    for name, content in (workspace_files or {}).items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if secrets:
        (config / "secrets.json").write_text(json.dumps(secrets), encoding="utf-8")
    env = os.environ.copy()
    env["HARNESS_CONFIG_DIR"] = str(config)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "harness.cli", "mcp", "--workspace", str(workspace),
        cwd=REPO_ROOT, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stderr_task = asyncio.create_task(process.stderr.read())
    responses, latencies = {}, {}
    try:
        for request in requests:
            request_id = request["id"]
            started = asyncio.get_running_loop().time()
            process.stdin.write((json.dumps(request) + "\n").encode())
            await asyncio.wait_for(process.stdin.drain(), 30)
            line = await asyncio.wait_for(process.stdout.readline(), (reply_timeouts or {}).get(request_id, 30))
            latencies[request_id] = asyncio.get_running_loop().time() - started
            assert line, f"MCP CLI exited before request {request_id} response"
            response = json.loads(line)
            assert response.get("id") == request_id
            responses[request_id] = response
    except BaseException as error:
        code, timed_out = await _stop_process(process)
        stderr = (await stderr_task).decode(errors="replace")
        error.add_note(f"MCP cleanup: exit={code}, timed_out={timed_out}, stderr={stderr}")
        raise
    code, timed_out = await _stop_process(process)
    stderr = (await stderr_task).decode(errors="replace")
    assert not timed_out, "MCP CLI failed to shut down after stdin EOF"
    assert code == 0, stderr
    return responses, workspace, latencies


def _request(request_id: int, method: str, params: dict[str, Any] | None = None):
    request = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return request


@pytest.mark.asyncio
async def test_cli_executes_exported_read_through_gate_with_validation_and_redaction(tmp_path):
    sentinel = "mcp-synthetic-redaction-sentinel"
    requests = [_request(1, "initialize"), _request(2, "tools/list"),
        _request(3, "tools/call", {"name": "view_file", "arguments": {"file_path": "visible.txt"}}),
        _request(4, "tools/call", {"name": "view_file", "arguments": {}}),
        _request(5, "tools/call", {"name": "view_file", "arguments": {"file_path": "../outside.txt"}})]
    responses, workspace, _ = await _run_mcp_cli(tmp_path,
        {"plugins": {"bridge-mcp-server": {"expose": ["view_file"]}}}, requests,
        secrets={"MCP_TEST_SENTINEL": sentinel}, workspace_files={"visible.txt": f"visible: {sentinel}\n"})
    assert [tool["name"] for tool in responses[2]["result"]["tools"]] == ["view_file"]
    result = responses[3]["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert "visible:" in text and "[REDACTED SECRET]" in text and sentinel not in text
    assert responses[4]["result"]["isError"] is True
    assert "Invalid arguments" in responses[4]["result"]["content"][0]["text"]
    assert responses[5]["result"]["isError"] is True
    assert "Path outside workspace" in responses[5]["result"]["content"][0]["text"]
    sessions = list((workspace / ".harness/sessions").iterdir())
    assert len(sessions) == 1
    metadata = json.loads((sessions[0] / "session.json").read_text())
    assert metadata["session_id"] == sessions[0].name
    assert metadata["metadata"] == {"transport": "mcp_stdio", "principal": "mcp_stdio"}
    transcript = (sessions[0] / "transcript.jsonl").read_text()
    assert '"kind": "tool.decision"' in transcript and sentinel not in transcript


@pytest.mark.asyncio
async def test_cli_locked_preset_denies_mutation_before_handler(tmp_path):
    requests = [_request(1, "initialize"), _request(2, "tools/list"), _request(3, "tools/call", {
        "name": "write_to_file", "arguments": {"file_path": "must-not-be-created.txt", "content": "handler ran"}})]
    responses, workspace, _ = await _run_mcp_cli(tmp_path, {"kernel": {"security": {"preset": "locked"}},
        "plugins": {"bridge-mcp-server": {"expose": ["write_to_file"]}}}, requests)
    assert [tool["name"] for tool in responses[2]["result"]["tools"]] == ["write_to_file"]
    assert responses[3]["result"]["isError"] is True
    assert "denied in plan mode" in responses[3]["result"]["content"][0]["text"]
    assert not (workspace / "must-not-be-created.txt").exists()


@pytest.mark.asyncio
async def test_cli_headless_write_approval_is_denied_by_real_broker_promptly(tmp_path):
    requests = [_request(1, "initialize"), _request(2, "tools/list"), _request(3, "tools/call", {
        "name": "write_to_file", "arguments": {"file_path": "headless-write.txt", "content": "handler ran"}})]
    responses, workspace, latencies = await _run_mcp_cli(tmp_path,
        {"plugins": {"bridge-mcp-server": {"expose": ["write_to_file"]}}}, requests, reply_timeouts={3: 1.0})
    assert [tool["name"] for tool in responses[2]["result"]["tools"]] == ["write_to_file"]
    assert responses[3]["result"]["isError"] is True
    assert "Approval denied: non-interactive session" in responses[3]["result"]["content"][0]["text"]
    assert latencies[3] < 1.0
    assert not (workspace / "headless-write.txt").exists()

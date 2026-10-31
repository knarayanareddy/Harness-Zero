"""Actual stdio MCP audit and read-ledger coverage plus narrow protocol controls."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from harness.plugins.bridges.mcp_server.server import MCPServer
from harness.sdk.types import (
    ImageBlock,
    RiskClass,
    TextBlock,
    ToolCall,
    ToolResult,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@asynccontextmanager
async def _mcp_connection(workspace: Path, config: Path):
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
        cwd=REPO_ROOT,
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


async def _call(process: asyncio.subprocess.Process, request_id: str, method: str, params: dict[str, Any] | None = None):
    assert process.stdin is not None and process.stdout is not None
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
    await asyncio.wait_for(process.stdin.drain(), timeout=2)
    line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
    assert line, f"MCP CLI exited before request {request_id} response"
    response = json.loads(line)
    assert response.get("id") == request_id
    return response


@pytest.mark.asyncio
async def test_actual_cli_audit_pairs_calls_and_results_and_scopes_reads_to_session(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    sentinel = "mcp-audit-synthetic-secret-8b482"
    (workspace / "existing.txt").write_text(f"original content {sentinel}", encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "kernel": {"security": {"preset": "open"}},
        "plugins": {"bridge-mcp-server": {"expose": ["view_file", "write_to_file"]}},
    }), encoding="utf-8")
    (config / "secrets.json").write_text(json.dumps({"MCP_AUDIT_SENTINEL": sentinel}), encoding="utf-8")

    async with _mcp_connection(workspace, config) as first:
        assert (await _call(first, "init-a", "initialize"))["result"]["serverInfo"]["name"]

        unread = await _call(first, "unread", "tools/call", {"name": "write_to_file", "arguments": {
            "file_path": "existing.txt", "content": "unread mutation"}})
        assert unread["result"]["isError"] is True
        assert "has not been read" in unread["result"]["content"][0]["text"]

        read = await _call(first, "read", "tools/call", {"name": "view_file", "arguments": {"file_path": "existing.txt"}})
        read_text = read["result"]["content"][0]["text"]
        assert read["result"]["isError"] is False
        assert "[REDACTED SECRET]" in read_text and sentinel not in read_text

        written = await _call(first, "write", "tools/call", {"name": "write_to_file", "arguments": {
            "file_path": "existing.txt", "content": sentinel}})
        assert written["result"]["isError"] is False
        assert (workspace / "existing.txt").read_text(encoding="utf-8") == sentinel

        # A result must be recorded in sanitized form too; the raw gate arguments above
        # remain unchanged and the real file contains the caller's exact content.
        reread = await _call(first, "reread", "tools/call", {"name": "view_file", "arguments": {"file_path": "existing.txt"}})
        assert reread["result"]["isError"] is False
        assert "[REDACTED SECRET]" in reread["result"]["content"][0]["text"]
        assert sentinel not in reread["result"]["content"][0]["text"]

        failed_read = await _call(first, "failed-read", "tools/call", {"name": "view_file", "arguments": {"file_path": "missing.txt"}})
        assert failed_read["result"]["isError"] is True
        (workspace / "missing.txt").write_text("created after failed read", encoding="utf-8")
        failed_read_write = await _call(first, "failed-read-write", "tools/call", {"name": "write_to_file", "arguments": {
            "file_path": "missing.txt", "content": "must remain unchanged"}})
        assert failed_read_write["result"]["isError"] is True
        assert "has not been read" in failed_read_write["result"]["content"][0]["text"]
        assert (workspace / "missing.txt").read_text(encoding="utf-8") == "created after failed read"

        assert (await _call(first, "stale-read", "tools/call", {"name": "view_file", "arguments": {"file_path": "existing.txt"}}))["result"]["isError"] is False
        (workspace / "existing.txt").write_text("external change", encoding="utf-8")
        future = time.time() + 5
        os.utime(workspace / "existing.txt", (future, future))
        stale_write = await _call(first, "stale-write", "tools/call", {"name": "write_to_file", "arguments": {
            "file_path": "existing.txt", "content": "must not overwrite stale content"}})
        assert stale_write["result"]["isError"] is True
        assert "changed since it was read" in stale_write["result"]["content"][0]["text"]
        assert (workspace / "existing.txt").read_text(encoding="utf-8") == "external change"

    # A separate stdio process owns a fresh MCP session; its overwrite is not
    # authorized by a successful read in the first connection.
    async with _mcp_connection(workspace, config) as second:
        assert (await _call(second, "init-b", "initialize"))["result"]["serverInfo"]["name"]
        cross_connection = await _call(second, "cross-connection", "tools/call", {"name": "write_to_file", "arguments": {
            "file_path": "existing.txt", "content": "cross-connection overwrite"}})
        assert cross_connection["result"]["isError"] is True
        assert "has not been read" in cross_connection["result"]["content"][0]["text"]

    from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore

    sessions = sorted((workspace / ".harness" / "sessions").iterdir())
    assert len(sessions) == 2
    reopened_sessions = [await JsonlTranscriptStore(session).read(compact=False) for session in sessions]
    reopened = max(
        reopened_sessions,
        key=lambda records: sum(len(record.get("tool_calls", [])) for record in records),
    )
    calls = {
        call["id"]: (record, call)
        for record in reopened
        for call in record.get("tool_calls", [])
    }
    results = {
        result["tool_use_id"]: result
        for record in reopened
        for result in record.get("tool_results", [])
    }
    assert len(calls) == len(results) == 8
    assert calls.keys() == results.keys()
    assert len(calls) == len(set(calls))
    assert all(record["kind"] == "assistant.message" for record, _ in calls.values())
    assert all(record["role"] == "assistant" for record, _ in calls.values())
    assert all(record["metadata"]["origin"] == "external_mcp" for record, _ in calls.values())
    assert all(record["metadata"]["model_generated"] is False for record, _ in calls.values())
    write_call = next(call for _, call in calls.values() if call["name"] == "write_to_file" and call["arguments"]["file_path"] == "existing.txt" and "[REDACTED SECRET]" in call["arguments"]["content"])
    assert write_call["arguments"]["content"] == "[REDACTED SECRET]"
    assert all(sentinel not in json.dumps(record, sort_keys=True) for record in reopened)
    call_counts = sorted(sum(len(record.get("tool_calls", [])) for record in records) for records in reopened_sessions)
    assert call_counts == [1, 8]


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel", [
    "mcp-enum-secret-8b482",
    "mcp-enum-secret-'quote\"-back\\slash-8b482",
])
@pytest.mark.parametrize("secret_source", ["file", "environment"])
async def test_validation_enum_error_redacts_audit_text_copies(
    tmp_path: Path, sentinel: str, secret_source: str, monkeypatch: pytest.MonkeyPatch,
):
    workspace, config = tmp_path / "workspace", tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({
        "plugins": {"bridge-mcp-server": {"expose": ["search_memory"]}},
    }))
    if secret_source == "file":
        (config / "secrets.json").write_text(json.dumps({"MCP_ENUM_SECRET": sentinel}))
    else:
        monkeypatch.setenv("MCP_ENUM_SECRET", sentinel)
    async with _mcp_connection(workspace, config) as process:
        await _call(process, "init", "initialize")
        response = await _call(process, "invalid-enum", "tools/call", {
            "name": "search_memory", "arguments": {"query": "fixture", "scope": sentinel},
        })
        assert response["result"]["isError"] is True
        assert "Invalid arguments" in response["result"]["content"][0]["text"]
    sessions = list((workspace / ".harness/sessions").iterdir())
    assert len(sessions) == 1
    records = [json.loads(line) for line in (sessions[0] / "transcript.jsonl").read_text().splitlines()]
    calls = [call for record in records for call in record.get("tool_calls", [])]
    results = [result for record in records for result in record.get("tool_results", [])]
    assert len(calls) == len(results) == 1
    assert calls[0]["id"] == results[0]["tool_use_id"]
    assert calls[0]["arguments"]["scope"] == "[REDACTED SECRET]"
    text = results[0]["content"][0]["text"]
    assert results[0]["is_error"] is True
    assert "Invalid arguments" in text and "[REDACTED SECRET]" in text
    assert sentinel not in text and repr(sentinel) not in text
    assert json.dumps(sentinel) not in text


@pytest.mark.asyncio
async def test_server_rejects_non_object_arguments_before_executor_or_audit():
    spec = ToolSpec(name="view_file", description="read", input_schema={"type": "object"}, risk_class=RiskClass.READ)
    started: list[Any] = []

    async def executor(call):
        started.append(call)
        raise AssertionError("invalid argument container must not reach executor")

    server = MCPServer(lambda: [spec], executor, expose_list=["view_file"])
    response = await server.handle_request({
        "jsonrpc": "2.0", "id": 42, "method": "tools/call",
        "params": {"name": "view_file", "arguments": ["not", "an", "object"]},
    })
    assert response is not None
    assert response["id"] == 42
    assert response["error"]["code"] == -32602
    assert started == []


class _MemoryAuditLog:
    """Small append-injection unit fixture; not an MCP CLI/e2e executor."""

    def __init__(self, fail_on: set[int] | None = None):
        self.fail_on = fail_on or set()
        self.append_count = 0
        self.records: list[dict[str, Any]] = []

    async def append(self, record):
        self.append_count += 1
        if self.append_count in self.fail_on:
            raise OSError("injected transcript append failure")
        data = record.to_dict()
        data["step_index"] = self.append_count
        self.records.append(data)
        return self.append_count


class _AuditSession:
    def __init__(self, log: _MemoryAuditLog):
        self.log = log
        self.session_id = "mcp-audit-unit"
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def turn_lock(self):
        async with self._lock:
            yield


class _AuditGate:
    def __init__(self, result: ToolResult | None = None):
        self.secrets = SimpleNamespace(redaction_values=lambda: ("unit-secret-value",))
        self.result = result or ToolResult.success("done")
        self.effects = 0

    async def execute(self, call, **kwargs):
        self.effects += 1
        return self.result


def _server_with_executor(executor):
    spec = ToolSpec(name="unit_tool", description="unit", input_schema={"type": "object"})
    return MCPServer(lambda: [spec], executor, expose_list=["unit_tool"])


@pytest.mark.asyncio
async def test_external_audit_replays_paired_calls_without_orphan_results():
    from harness.cli import _make_mcp_tool_executor
    from harness.kernel.context import derive_messages

    log = _MemoryAuditLog()
    execute = _make_mcp_tool_executor(_AuditGate(), _AuditSession(log))
    await execute(ToolCall(id="external-call", name="unit_tool", arguments={"value": "fixture"}))
    projected = derive_messages(log.records)
    calls = [block for message in projected for block in message.content if isinstance(block, ToolUseBlock)]
    results = [block for message in projected for block in message.content if isinstance(block, ToolResultBlock)]
    assert len(calls) == len(results) == 1
    assert calls[0].id == results[0].tool_use_id == "external-call"
    assert all(message.metadata["origin"] == "external_mcp" for message in projected)
    assert all(message.metadata["model_generated"] is False for message in projected)


@pytest.mark.asyncio
async def test_audit_refreshes_secret_sources_resolved_during_execution(tmp_path, monkeypatch):
    from harness.cli import _make_mcp_tool_executor
    from harness.kernel.secrets import Secrets

    sentinel = "synthetic-late-resolved-value-83741"
    monkeypatch.setenv("OPAQUE_VENDOR_NAME", sentinel)

    class ResolvingGate:
        secrets = Secrets(tmp_path / "secrets.json")

        async def execute(self, call, **kwargs):
            return ToolResult.success(self.secrets.get("OPAQUE_VENDOR_NAME"))

    log = _MemoryAuditLog()
    execute = _make_mcp_tool_executor(ResolvingGate(), _AuditSession(log))
    result = await execute(ToolCall(id="late-resolved", name="unit_tool", arguments={}))
    assert result.content[0].text == sentinel, "Audit redaction must not mutate the caller's result"
    assert log.records[-1]["tool_results"][0]["content"][0]["text"] == "[REDACTED SECRET]"


@pytest.mark.asyncio
async def test_result_secret_discovery_failure_fences_after_effect():
    from harness.cli import _make_mcp_tool_executor

    gate = _AuditGate()

    def discover():
        if gate.effects:
            raise OSError("injected discovery failure")
        return ()

    gate.secrets = SimpleNamespace(redaction_values=discover)
    log = _MemoryAuditLog()
    server = _server_with_executor(_make_mcp_tool_executor(gate, _AuditSession(log)))
    for request_id in ("first", "second"):
        response = await server.handle_request({
            "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": "unit_tool", "arguments": {}},
        })
        assert response["result"]["isError"] is True
        text = response["result"]["content"][0]["text"]
        assert "effect may have occurred" in text and "connection is fenced" in text
    assert gate.effects == 1 and len(log.records) == 1


@pytest.mark.asyncio
async def test_secret_file_discovery_error_returns_no_private_path_or_effect(tmp_path, monkeypatch):
    from harness.cli import _make_mcp_tool_executor
    from harness.kernel.secrets import Secrets

    gate = _AuditGate()
    gate.secrets = Secrets(tmp_path / "private-config/secrets.json")
    gate.secrets.set("MCP_TEST_SECRET", "synthetic-private-secret-38917")
    read_text = Path.read_text

    def unavailable(path, *args, **kwargs):
        if path == gate.secrets.path:
            raise PermissionError(13, "synthetic private diagnostic", str(path))
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unavailable)
    log = _MemoryAuditLog()
    server = _server_with_executor(_make_mcp_tool_executor(gate, _AuditSession(log)))
    response = await server.handle_request({
        "jsonrpc": "2.0", "id": "discovery-failure", "method": "tools/call",
        "params": {"name": "unit_tool", "arguments": {}},
    })
    assert response["result"]["isError"] is True
    text = response["result"]["content"][0]["text"]
    assert "execution was not started" in text
    assert str(gate.secrets.path) not in text and "synthetic private diagnostic" not in text
    assert "synthetic-private-secret-38917" not in text
    assert gate.effects == 0 and log.records == []


@pytest.mark.asyncio
async def test_audit_append_failures_block_or_fence_execution_after_effect():
    from harness.cli import _make_mcp_tool_executor

    pre_log = _MemoryAuditLog(fail_on={1})
    pre_gate = _AuditGate()
    pre_server = _server_with_executor(_make_mcp_tool_executor(pre_gate, _AuditSession(pre_log)))
    pre_failure = await pre_server.handle_request({
        "jsonrpc": "2.0", "id": "before", "method": "tools/call",
        "params": {"name": "unit_tool", "arguments": {}},
    })
    assert pre_failure["result"]["isError"] is True
    assert "execution was not started" in pre_failure["result"]["content"][0]["text"]
    assert pre_gate.effects == 0
    assert pre_log.records == []
    pre_fenced = await pre_server.handle_request({
        "jsonrpc": "2.0", "id": "before-again", "method": "tools/call",
        "params": {"name": "unit_tool", "arguments": {}},
    })
    assert pre_fenced["result"]["isError"] is True
    assert pre_gate.effects == 0

    result_log = _MemoryAuditLog(fail_on={2})
    result_gate = _AuditGate()
    result_server = _server_with_executor(_make_mcp_tool_executor(result_gate, _AuditSession(result_log)))
    uncertain = await result_server.handle_request({
        "jsonrpc": "2.0", "id": "after", "method": "tools/call",
        "params": {"name": "unit_tool", "arguments": {}},
    })
    message = uncertain["result"]["content"][0]["text"]
    assert uncertain["result"]["isError"] is True
    assert "effect may have occurred" in message and "connection is fenced" in message
    assert result_gate.effects == 1
    assert len(result_log.records) == 1
    fenced = await result_server.handle_request({
        "jsonrpc": "2.0", "id": "after-again", "method": "tools/call",
        "params": {"name": "unit_tool", "arguments": {}},
    })
    assert fenced["result"]["isError"] is True
    assert "connection is fenced" in fenced["result"]["content"][0]["text"]
    assert result_gate.effects == 1


@pytest.mark.asyncio
async def test_executor_records_typed_image_and_cancelled_pre_action_result():
    from harness.cli import _make_mcp_tool_executor

    log = _MemoryAuditLog()
    gate = _AuditGate(ToolResult(content=[ImageBlock(media_type="image/png", data_b64="AA=="), TextBlock("ok")]))
    execute = _make_mcp_tool_executor(gate, _AuditSession(log))
    result = await execute(ToolCall(id="unique-a", name="unit_tool", arguments={}))
    assert result.is_error is False
    logged_result = log.records[-1]["tool_results"][0]
    assert logged_result["content"][0] == {"type": "image", "media_type": "image/png", "data_b64": "AA=="}
    assert "metadata" not in logged_result

    cancelled = asyncio.Event()
    cancelled.set()
    cancelled_result = await execute(
        ToolCall(id="unique-b", name="unit_tool", arguments={}),
        cancel_event=cancelled,
    )
    assert cancelled_result.is_error is True
    assert gate.effects == 1, "a cancelled call must not start the gate handler"
    assert log.records[-1]["tool_results"][0]["is_error"] is True
    assert log.records[-1]["tool_results"][0]["tool_use_id"] == "unique-b"

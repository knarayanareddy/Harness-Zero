"""T18's remaining audit/read-before-edit seam, separate from cancellation."""
from __future__ import annotations

import json

import pytest

from tests.plugins.bridges.test_mcp_cli_execution import _request, _run_mcp_cli


@pytest.mark.asyncio
async def test_T18_mcp_read_is_audited_and_authorizes_only_a_later_guarded_edit(tmp_path):
    requests = [
        _request(1, "initialize"),
        _request(2, "tools/call", {"name": "write_to_file", "arguments": {
            "file_path": "existing.txt", "content": "unread mutation must not run"}}),
        _request(3, "tools/call", {"name": "view_file", "arguments": {"file_path": "existing.txt"}}),
        _request(4, "tools/call", {"name": "write_to_file", "arguments": {
            "file_path": "existing.txt", "content": "read-backed mutation"}}),
    ]
    responses, workspace, _ = await _run_mcp_cli(tmp_path, {
        "kernel": {"security": {"preset": "open"}},
        "plugins": {"bridge-mcp-server": {"expose": ["view_file", "write_to_file"]}},
    }, requests, workspace_files={"existing.txt": "original known content"})
    assert responses[2]["result"]["isError"] is True
    assert "has not been read" in str(responses[2])
    assert responses[3]["result"]["isError"] is False
    assert "original known content" in str(responses[3])
    assert responses[4]["result"]["isError"] is False, "A real successful read must enter the gate's session ledger"
    assert (workspace / "existing.txt").read_text() == "read-backed mutation"
    sessions = list((workspace / ".harness/sessions").iterdir())
    assert len(sessions) == 1
    records = [json.loads(line) for line in (sessions[0] / "transcript.jsonl").read_text().splitlines()]
    calls = {call["id"]: call for record in records for call in record.get("tool_calls", [])}
    results = {result["tool_use_id"]: result for record in records for result in record.get("tool_results", [])}
    assert len(calls) == len(results) == 3
    assert calls.keys() == results.keys()
    read_id = next(key for key, call in calls.items() if call["name"] == "view_file")
    assert results[read_id]["is_error"] is False

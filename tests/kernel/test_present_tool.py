"""WS-07: `present` is read-only, path-confined, and durable before returning its notice card."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from harness.plugins.tools.deliverables.tools import PRESENT_SPEC, handle_present
from harness.sdk.types import RiskClass


class RecordingToolContext:
    def __init__(self, workspace: Path, *, turn_id: str = "turn_ws07") -> None:
        self.workspace = workspace
        self.turn_id = turn_id
        self.call_id = "call_present"
        self.session_id = "session_present"
        self.turn_state: dict[str, Any] = {}
        self.data_dir = workspace / ".harness"
        self.capabilities = frozenset()
        self.emit_record = AsyncMock()


def test_present_tool_is_read_only_and_has_closed_schema() -> None:
    assert PRESENT_SPEC.risk_class == RiskClass.READ
    assert PRESENT_SPEC.capabilities == frozenset({"fs:read"})
    assert PRESENT_SPEC.input_schema["additionalProperties"] is False
    assert set(PRESENT_SPEC.input_schema["properties"]) == {"path", "caption"}


@pytest.mark.asyncio
async def test_present_emits_payload_hash_and_notice_card(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "report.md"
    raw = b"# Result\n\nA durable deliverable.\n"
    target.write_bytes(raw)
    ctx = RecordingToolContext(workspace)

    result = await handle_present({"path": "report.md", "caption": "Final report"}, ctx)

    assert not result.is_error
    assert len(result.cards) == 1 and result.cards[0].kind == "notice"
    payload = ctx.emit_record.await_args.args[0]
    assert payload.EVENT_TYPE == "deliverable.presented"
    assert payload.to_dict() == {
        "turn_id": "turn_ws07",
        "path": "report.md",
        "caption": "Final report",
        "kind": "file",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert ctx.emit_record.await_args.kwargs["metadata"] == payload.to_dict()
    assert "Presented report.md" in result.cards[0].data["body"]


@pytest.mark.asyncio
async def test_present_accepts_artifact_filename_and_records_canonical_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    artifacts = workspace / ".harness" / "artifacts"
    artifacts.mkdir(parents=True)
    target = artifacts / "chart.svg"
    target.write_text("<svg/>", encoding="utf-8")
    ctx = RecordingToolContext(workspace)

    result = await handle_present({"path": "chart.svg"}, ctx)

    assert not result.is_error
    payload = ctx.emit_record.await_args.args[0]
    assert payload.path == ".harness/artifacts/chart.svg"
    assert payload.kind == "artifact"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../outside.txt", ".harness/settings.json", ".git/config"])
async def test_present_refuses_traversal_and_harness_internals(tmp_path: Path, path: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = RecordingToolContext(workspace)

    result = await handle_present({"path": path}, ctx)

    assert result.is_error
    ctx.emit_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_present_resolves_artifact_basename_after_jail_normalization(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    artifact = workspace / ".harness" / "artifacts" / "chart.svg"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("<svg/>", encoding="utf-8")
    ctx = RecordingToolContext(workspace)

    # PolicyGate normalizes the model's relative `path` to this absolute workspace-root path before the handler.
    result = await handle_present({"path": str(workspace / "chart.svg")}, ctx)

    assert not result.is_error
    payload = ctx.emit_record.await_args.args[0]
    assert payload.path == ".harness/artifacts/chart.svg"
    assert payload.kind == "artifact"


@pytest.mark.asyncio
async def test_present_refuses_symlinked_artifact_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    private = workspace / ".harness" / "private"
    private.mkdir(parents=True)
    (private / "secret.md").write_text("private checkpoint data", encoding="utf-8")
    (workspace / ".harness" / "artifacts").symlink_to(private, target_is_directory=True)
    ctx = RecordingToolContext(workspace)

    result = await handle_present({"path": "secret.md"}, ctx)

    assert result.is_error
    ctx.emit_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_present_refuses_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "secret-link.txt").symlink_to(outside)
    ctx = RecordingToolContext(workspace)

    result = await handle_present({"path": "secret-link.txt"}, ctx)

    assert result.is_error
    ctx.emit_record.assert_not_awaited()

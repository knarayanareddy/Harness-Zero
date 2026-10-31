"""Session metadata stays durable and read-only across process restarts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ModelInfo, TextDelta, ToolCallEnd, ToolResult, ToolSpec


def _registry(workspace: Path) -> PluginRegistry:
    config = workspace / "config"
    return PluginRegistry(
        workspace=workspace,
        settings=SettingsStore(config / "settings.json"),
        secrets=Secrets(config / "secrets.json"),
    )


@pytest.mark.asyncio
async def test_restart_load_preserves_top_level_creation_time_and_saved_bytes(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = _registry(workspace)
    first_manager = SessionManager(workspace, registry)

    with patch("harness.kernel.session.time.time", return_value=1234.5):
        original = await first_manager.create_session(
            "session_000000000024", metadata={"title": "Restart metadata"}
        )

    metadata_path = original.session_dir / "session.json"
    saved = {
        "session_id": original.session_id,
        "workspace": str(workspace),
        "created_at": 1234.5,
        "metadata": {
            "title": "Restart metadata",
            "created_at": 2222.5,
            "custom": {"label": "雪 ☃", "values": [1, None, {"ok": True}]},
        },
        "unknown_top_level": {"keep": ["exactly", "as", "written"]},
    }
    metadata_bytes = (json.dumps(saved, ensure_ascii=False, indent=1) + "\n").encode()
    metadata_path.write_bytes(metadata_bytes)
    compact_path = original.session_dir / "transcript.jsonl"
    full_path = original.session_dir / "transcript_full.jsonl"
    compact_path.write_bytes(b'{"id":"canonical-call-1","content":"hello"}\n')
    full_path.write_bytes(b'{"id":"canonical-call-1","content":"hello","detail":"full"}\n')
    transcript_hashes = (compact_path.read_bytes(), full_path.read_bytes())

    restarted = SessionManager(workspace, registry)
    with patch("harness.kernel.session.time.time", return_value=9876.5):
        loaded = restarted.get_session(original.session_id)

    assert loaded is not None
    assert loaded.created_at == 1234.5
    assert loaded.metadata == saved["metadata"]
    assert metadata_path.read_bytes() == metadata_bytes
    assert (compact_path.read_bytes(), full_path.read_bytes()) == transcript_hashes


@pytest.mark.asyncio
async def test_new_session_persists_top_level_creation_time(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = SessionManager(workspace, _registry(workspace))

    with patch("harness.kernel.session.time.time", return_value=1234.5):
        session = await manager.create_session(
            "session_000000000025", metadata={"title": "New session"}
        )

    saved = json.loads((session.session_dir / "session.json").read_text())
    assert saved["created_at"] == 1234.5
    assert saved["metadata"] == {"title": "New session"}


@pytest.mark.asyncio
async def test_existing_session_without_metadata_uses_fallback_without_writing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    session_dir = workspace / ".harness" / "sessions" / "session_000000000026"
    session_dir.mkdir(parents=True)
    fallback = session_dir.stat().st_mtime
    manager = SessionManager(workspace, _registry(workspace))

    with patch("harness.kernel.session.time.time", return_value=9876.5):
        loaded = manager.get_session(session_dir.name)

    assert loaded is not None
    assert loaded.created_at == fallback
    assert not (session_dir / "session.json").exists()


@pytest.mark.asyncio
async def test_corrupt_metadata_is_not_rewritten_on_read(tmp_path: Path):
    workspace = tmp_path / "workspace"
    session_dir = workspace / ".harness" / "sessions" / "session_000000000027"
    session_dir.mkdir(parents=True)
    metadata_path = session_dir / "session.json"
    corrupt_bytes = b'{ "created_at": 17, "unknown": [\xff\x00\n'
    metadata_path.write_bytes(corrupt_bytes)
    fallback = session_dir.stat().st_mtime
    manager = SessionManager(workspace, _registry(workspace))

    with patch("harness.kernel.session.time.time", return_value=9876.5):
        loaded = manager.get_session(session_dir.name)

    assert loaded is not None
    assert loaded.created_at == fallback
    assert metadata_path.read_bytes() == corrupt_bytes


@pytest.mark.asyncio
async def test_non_finite_top_level_time_falls_back_without_rewriting_metadata(tmp_path: Path):
    workspace = tmp_path / "workspace"
    session_dir = workspace / ".harness" / "sessions" / "session_000000000029"
    session_dir.mkdir(parents=True)
    metadata_path = session_dir / "session.json"
    metadata_bytes = b'{ "created_at": 1e999, "metadata": {"created_at": 123} }\n'
    metadata_path.write_bytes(metadata_bytes)
    fallback = session_dir.stat().st_mtime
    manager = SessionManager(workspace, _registry(workspace))

    with patch("harness.kernel.session.time.time", return_value=9876.5):
        loaded = manager.get_session(session_dir.name)

    assert loaded is not None
    assert loaded.created_at == fallback
    assert loaded.metadata == {"created_at": 123}
    assert metadata_path.read_bytes() == metadata_bytes


@pytest.mark.parametrize(
    ("created_at", "case"),
    [
        (True, "boolean"),
        ("1234.5", "numeric-string"),
        (float("inf"), "infinity"),
        (10**400, "too-large-integer"),
    ],
)
@pytest.mark.parametrize("first_reader", ["get", "list"])
def test_invalid_creation_time_has_same_filesystem_fallback_for_get_and_list(
    tmp_path: Path, created_at: object, case: str, first_reader: str
):
    workspace = tmp_path / "workspace"
    session_dir = workspace / ".harness" / "sessions" / "session_000000000030"
    session_dir.mkdir(parents=True)
    metadata_path = session_dir / "session.json"
    metadata_bytes = (
        b'{"created_at": '
        + json.dumps(created_at, allow_nan=True).encode("utf-8")
        + b', "metadata": {"title": "Keep me"}}\n'
    )
    metadata_path.write_bytes(metadata_bytes)
    fallback = 2345.25
    os.utime(session_dir, (fallback, fallback))
    manager = SessionManager(workspace, _registry(workspace))

    readers = [first_reader, "list" if first_reader == "get" else "get"]
    observed: list[float] = []
    for reader in readers:
        if reader == "get":
            loaded = manager.get_session(session_dir.name)
            assert loaded is not None, case
            assert loaded.metadata == {"title": "Keep me"}, case
            observed.append(loaded.created_at)
        else:
            row = next(row for row in manager.list_sessions() if row["session_id"] == session_dir.name)
            observed.append(row["created_at"])

    assert observed == [fallback, fallback], case
    assert metadata_path.read_bytes() == metadata_bytes, case

    fresh_manager = SessionManager(workspace, _registry(workspace))
    fresh_session = fresh_manager.get_session(session_dir.name)
    fresh_row = next(row for row in fresh_manager.list_sessions() if row["session_id"] == session_dir.name)
    assert fresh_session is not None
    assert (fresh_session.created_at, fresh_row["created_at"]) == (fallback, fallback)


@pytest.mark.parametrize("created_at", [0, -1.5])
@pytest.mark.parametrize("first_reader", ["get", "list"])
def test_valid_zero_and_negative_creation_times_are_preserved(
    tmp_path: Path, created_at: int | float, first_reader: str
):
    workspace = tmp_path / "workspace"
    session_dir = workspace / ".harness" / "sessions" / "session_000000000034"
    session_dir.mkdir(parents=True)
    metadata_path = session_dir / "session.json"
    metadata_bytes = json.dumps(
        {"created_at": created_at, "metadata": {"created_at": 99}}, separators=(",", ":")
    ).encode("utf-8")
    metadata_path.write_bytes(metadata_bytes)
    manager = SessionManager(workspace, _registry(workspace))

    readers = [first_reader, "list" if first_reader == "get" else "get"]
    observed: list[float] = []
    for reader in readers:
        if reader == "get":
            loaded = manager.get_session(session_dir.name)
            assert loaded is not None
            assert loaded.metadata == {"created_at": 99}
            observed.append(loaded.created_at)
        else:
            row = next(row for row in manager.list_sessions() if row["session_id"] == session_dir.name)
            observed.append(row["created_at"])

    assert observed == [created_at, created_at]
    assert metadata_path.read_bytes() == metadata_bytes


@pytest.mark.parametrize(
    ("metadata_bytes", "expected_metadata", "expected_created_at"),
    [
        (b"[]", {}, None),
        (b"null", {}, None),
        (b'"scalar"', {}, None),
        (b"7", {}, None),
        (b'{"created_at": 1234.5, "metadata": []}', {}, 1234.5),
        (b'{"created_at": 1234.5, "metadata": "scalar"}', {}, 1234.5),
    ],
)
@pytest.mark.parametrize("first_reader", ["get", "list"])
def test_nonobject_metadata_shapes_are_safe_and_read_only_for_get_and_list(
    tmp_path: Path,
    metadata_bytes: bytes,
    expected_metadata: dict[str, object],
    expected_created_at: float | None,
    first_reader: str,
):
    workspace = tmp_path / "workspace"
    session_dir = workspace / ".harness" / "sessions" / "session_000000000031"
    session_dir.mkdir(parents=True)
    metadata_path = session_dir / "session.json"
    metadata_path.write_bytes(metadata_bytes)
    fallback = 3456.75
    os.utime(session_dir, (fallback, fallback))
    healthy_dir = workspace / ".harness" / "sessions" / "session_000000000032"
    healthy_dir.mkdir()
    (healthy_dir / "session.json").write_bytes(b'{"created_at": 4567.5, "metadata": {}}')
    manager = SessionManager(workspace, _registry(workspace))

    readers = [first_reader, "list" if first_reader == "get" else "get"]
    observed: list[float] = []
    for reader in readers:
        if reader == "get":
            loaded = manager.get_session(session_dir.name)
            assert loaded is not None
            assert loaded.metadata == expected_metadata
            observed.append(loaded.created_at)
        else:
            rows = manager.list_sessions()
            assert {row["session_id"] for row in rows} == {session_dir.name, healthy_dir.name}
            row = next(row for row in rows if row["session_id"] == session_dir.name)
            observed.append(row["created_at"])

    expected_time = fallback if expected_created_at is None else expected_created_at
    assert observed == [expected_time, expected_time]
    assert metadata_path.read_bytes() == metadata_bytes


@pytest.mark.asyncio
async def test_resume_last_keeps_nonobject_metadata_session_readable(tmp_path: Path):
    workspace = tmp_path / "workspace"
    session_dir = workspace / ".harness" / "sessions" / "session_000000000033"
    session_dir.mkdir(parents=True)
    metadata_path = session_dir / "session.json"
    metadata_bytes = b'[]\n'
    metadata_path.write_bytes(metadata_bytes)
    (session_dir / "transcript.jsonl").write_bytes(b'{"role":"user","content":"continue"}\n')
    fallback = 5678.25
    os.utime(session_dir, (fallback, fallback))
    manager = SessionManager(workspace, _registry(workspace))

    resumed = await manager.resume_last()

    assert resumed is not None
    assert resumed.session_id == session_dir.name
    assert resumed.created_at == fallback
    assert resumed.metadata == {}
    assert metadata_path.read_bytes() == metadata_bytes


@pytest.mark.asyncio
async def test_bootstrap_commands_restart_preserves_metadata_and_transcript(tmp_path: Path):
    from harness.kernel.bootstrap import bootstrap_kernel

    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    session_id = "session_000000000028"
    model = ModelInfo(id="metadata-test-v1", display_name="Metadata test")
    provider = ScriptedProvider(models=[model])
    provider.add_response(
        [
            ToolCallEnd(
                id="call_t24_metadata_1",
                name="metadata_marker",
                arguments={"label": "record-once"},
            ),
            Done(stop_reason="tool_use"),
        ]
    )
    provider.add_response([TextDelta(text="Completed once."), Done(stop_reason="end_turn")])
    marker_runs: list[str] = []

    async def mark(arguments: dict[str, str], _context: object) -> ToolResult:
        label = arguments["label"]
        marker_runs.append(label)
        return ToolResult.success(label)

    runtime = await bootstrap_kernel(workspace=workspace, config_dir=config)
    runtime.registry.register_provider(provider, plugin_name="metadata-test")
    runtime.registry.register_tool(
        ToolSpec(
            name="metadata_marker",
            description="Record a local test marker.",
            input_schema={
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "required": ["label"],
                "additionalProperties": False,
            },
        ),
        mark,
        plugin_name="metadata-test",
    )
    with patch("harness.kernel.session.time.time", return_value=1234.5):
        created = await runtime.commands.sessions_create(
            session_id=session_id,
            metadata={"title": "Bootstrap restart", "custom": {"unicode": "雪"}},
        )
    assert created["created_at"] == 1234.5
    await runtime.commands.submit_turn(
        session_id=session_id,
        prompt="Run the marker once.",
        options={"provider": "scripted", "model": model.id},
    )
    provider.assert_requested(count=2, model=model.id)
    assert marker_runs == ["record-once"]

    original_session = runtime.session_manager.get_session(session_id)
    assert original_session is not None
    metadata_path = original_session.session_dir / "session.json"
    compact_path = original_session.session_dir / "transcript.jsonl"
    full_path = original_session.session_dir / "transcript_full.jsonl"
    metadata_bytes = metadata_path.read_bytes()
    transcript_hashes = tuple(
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (compact_path, full_path)
    )
    before = await runtime.commands.sessions_get(session_id=session_id)
    canonical_ids = [
        call["id"]
        for record in before["records"]
        for call in record.get("tool_calls", [])
    ] + [
        result["tool_use_id"]
        for record in before["records"]
        for result in record.get("tool_results", [])
    ]
    assert canonical_ids == ["call_t24_metadata_1", "call_t24_metadata_1"]
    assert (await runtime.commands.sessions_list())[0]["created_at"] == 1234.5
    await runtime.registry.unload_all()

    with patch("harness.kernel.session.time.time", return_value=9876.5):
        restarted = await bootstrap_kernel(workspace=workspace, config_dir=config)
    assert restarted.session_manager.active_session is not None
    assert restarted.session_manager.active_session.session_id == session_id
    assert restarted.session_manager.active_session.created_at == 1234.5
    after = await restarted.commands.sessions_get(session_id=session_id)
    rows = await restarted.commands.sessions_list()

    assert after["created_at"] == 1234.5
    assert after["metadata"] == {"title": "Bootstrap restart", "custom": {"unicode": "雪"}}
    assert after["records"] == before["records"]
    assert rows[0]["created_at"] == 1234.5
    assert isinstance(restarted.session_manager.active_session.metadata, dict)
    assert restarted.session_manager.active_session.created_at == 1234.5
    assert metadata_path.read_bytes() == metadata_bytes
    assert tuple(
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (compact_path, full_path)
    ) == transcript_hashes
    assert marker_runs == ["record-once"]
    provider.assert_requested(count=2, model=model.id)
    await restarted.registry.unload_all()

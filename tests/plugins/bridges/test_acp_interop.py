"""Official ACP Python client interoperability tests for the v1 adapter."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from acp.exceptions import RequestError
from acp.schema import (
    AllowedOutcome,
    AudioContentBlock,
    DeniedOutcome,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    RequestPermissionResponse,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
)
from acp.stdio import spawn_agent_process

from harness.kernel.bootstrap import bootstrap_kernel
from harness.plugins.bridges.acp.server import ACPServer


def _evidence_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("T20B_EVIDENCE_DIR", os.environ.get("T20A_EVIDENCE_DIR", tmp_path)))


def _child_environment(root: Path, config: Path, home: Path, site: Path, probe: Path) -> dict[str, str]:
    site.mkdir(parents=True, exist_ok=True)
    (site / "sitecustomize.py").write_text(
        "import importlib.util, json, os\n"
        "from pathlib import Path\n"
        f"Path({str(probe)!r}).write_text(json.dumps({{"
        "'harness_origin': importlib.util.find_spec('harness').origin,"
        "'environment_names': sorted(os.environ),"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": os.pathsep.join((str(site), str(root))),
        "PYTHONUNBUFFERED": "1",
        "HARNESS_CONFIG_DIR": str(config),
    }


def _assert_child_provenance(probe: Path, root: Path, evidence: Path, name: str) -> None:
    data = json.loads(probe.read_text(encoding="utf-8"))
    assert data["harness_origin"] == str(root / "harness" / "__init__.py")
    allowed = {
        "HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER",
        "PYTHONPATH", "PYTHONUNBUFFERED", "HARNESS_CONFIG_DIR",
        "__CF_USER_TEXT_ENCODING", "LC_CTYPE",
    }
    assert set(data["environment_names"]) <= allowed
    assert "HARNESS_MOCK_SCRIPT" not in data["environment_names"]
    assert not any("KEY" in name or "TOKEN" in name or "SECRET" in name for name in data["environment_names"])
    assert not any("HERMES" in name or "IMPORT_OVERRIDE" in name for name in data["environment_names"])
    (evidence / f"{name}-child-provenance.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8",
    )


def test_v1_workspace_validation_rejects_absolute_path_with_nul(tmp_path: Path):
    server = ACPServer(commands=SimpleNamespace(workspace=tmp_path))
    invalid_cwd = f"{tmp_path}/\0invalid"
    assert Path(invalid_cwd).is_absolute()
    assert server._valid_v1_cwd(invalid_cwd) is False


@pytest.mark.asyncio
async def test_official_client_negotiates_and_owns_a_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use the pinned SDK over the public CLI stdio path, not a JSON-RPC fake."""
    root = Path(__file__).resolve().parents[3]
    workspace = tmp_path / "workspace"
    config = tmp_path / "config"
    home = tmp_path / "home"
    site = tmp_path / "child-site"
    evidence = _evidence_dir(tmp_path)
    for key in tuple(os.environ):
        upper = key.upper()
        if any(marker in upper for marker in (
            "API_KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL", "PRIVATE_KEY", "ACCESS_KEY",
        )):
            monkeypatch.delenv(key)
    monkeypatch.delenv("HARNESS_MOCK_SCRIPT", raising=False)
    for directory in (workspace, config, home, site):
        directory.mkdir(parents=True, exist_ok=True)
    previous = await bootstrap_kernel(workspace=workspace, config_dir=config, resume_session=False)
    foreign = await previous.commands.sessions_create(session_id="session_aaaaaaaaaaaa")
    await previous.registry.unload_all()
    marker = tmp_path / "child-harness-origin.txt"
    (site / "sitecustomize.py").write_text(
        "import importlib.util, pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text(importlib.util.find_spec('harness').origin)\n",
        encoding="utf-8",
    )
    child_env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": os.pathsep.join((str(site), str(root))),
        "PYTHONUNBUFFERED": "1",
        "HARNESS_CONFIG_DIR": str(config),
    }
    assert "HARNESS_MOCK_SCRIPT" not in child_env
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        message = getattr(event, "message", None)
        direction = getattr(getattr(event, "direction", None), "value", None)
        observed.append({"direction": direction, "message": message})

    wire_path = evidence / "sdk-wire.jsonl"
    stderr_task: asyncio.Task[bytes] | None = None
    stderr_text = ""
    child: object | None = None
    try:
        async with asyncio.timeout(20):
            async with spawn_agent_process(
                object(),
                sys.executable,
                "-m",
                "harness.cli",
                "acp",
                "--workspace",
                str(workspace),
                cwd=root,
                env=child_env,
                transport_kwargs={"shutdown_timeout": 1.0},
                observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                initialize = await connection.initialize(protocol_version=1)
                assert initialize.protocol_version == 1
                assert initialize.agent_info.name == "harness-zero-acp"
                assert initialize.agent_capabilities is not None
                assert initialize.auth_methods == []
                created = await connection.new_session(
                    cwd=str(workspace),
                    additional_directories=[],
                    mcp_servers=[],
                )
                listed = await connection.list_sessions(cwd=str(workspace))
                assert created.session_id
                assert [row.session_id for row in listed.sessions] == [created.session_id]
                assert created.session_id != foreign["session_id"]
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        cleanup_error = None
        if stderr_task is not None and not stderr_task.done():
            try:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
            except Exception as error:
                cleanup_error = f"stderr drain: {type(error).__name__}: {error}"
        wire_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (evidence / "child-stderr.log").write_text(stderr_text, encoding="utf-8")
        (evidence / "sdk-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
            "stderr_drain_error": cleanup_error,
        }, indent=2) + "\n", encoding="utf-8")
    assert getattr(child, "returncode", None) == 0
    child_origin = marker.read_text(encoding="utf-8")
    assert child_origin == str(root / "harness" / "__init__.py")
    (evidence / "child-provenance.json").write_text(json.dumps({
        "harness_origin": child_origin,
        "expected_worker_origin": str(root / "harness" / "__init__.py"),
    }, indent=2) + "\n", encoding="utf-8")


async def _raw_rpc(
    *, root: Path, workspace: Path, config: Path, home: Path, requests: list[dict[str, object]],
    name: str, evidence: Path,
) -> list[dict[str, object]]:
    """Exchange bounded raw requests with the real CLI for malformed-boundary controls."""
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(root),
        "PYTHONUNBUFFERED": "1",
        "HARNESS_CONFIG_DIR": str(config),
    }
    assert "HARNESS_MOCK_SCRIPT" not in env
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "harness.cli",
        "acp",
        "--workspace",
        str(workspace),
        cwd=root,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    stderr_task = asyncio.create_task(process.stderr.read())
    observed: list[dict[str, object]] = []
    replies: list[dict[str, object]] = []
    try:
        async with asyncio.timeout(20):
            for request in requests:
                observed.append({"direction": "outgoing", "message": request})
                process.stdin.write(json.dumps(request).encode() + b"\n")
                await process.stdin.drain()
                line = await asyncio.wait_for(process.stdout.readline(), timeout=2)
                assert line, f"CLI exited before a response for {request['method']}"
                response = json.loads(line)
                observed.append({"direction": "incoming", "message": response})
                replies.append(response)
    finally:
        process.stdin.close()
        try:
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass
        stdout_text = b""
        stderr_text = b""
        try:
            async with asyncio.timeout(3):
                _, stdout_text, stderr_raw = await asyncio.gather(
                    process.wait(), process.stdout.read(), stderr_task
                )
                stderr_text = stderr_raw.decode("utf-8", errors="replace")
        finally:
            (evidence / f"{name}-wire.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
                encoding="utf-8",
            )
            (evidence / f"{name}-stderr.log").write_text(stderr_text, encoding="utf-8")
            (evidence / f"{name}-cleanup.json").write_text(json.dumps({
                "pid": process.pid,
                "returncode": process.returncode,
                "stderr_bytes": len(stderr_text.encode("utf-8")),
                "stdout_empty": not stdout_text.strip(),
            }, indent=2) + "\n", encoding="utf-8")
        assert process.returncode == 0, stderr_text[-4096:]
        assert not stdout_text.strip(), "CLI emitted a non-RPC stdout line"
    return replies


@pytest.mark.asyncio
async def test_v1_negotiation_and_session_bounds_are_fail_closed(tmp_path: Path):
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    evidence = _evidence_dir(tmp_path)
    for directory in (workspace, config, home):
        directory.mkdir(parents=True, exist_ok=True)
    other = tmp_path / "other-workspace"
    other.mkdir()
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {
            "cwd": str(workspace), "mcpServers": [], "additionalDirectories": [],
        }},
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": True}},
        {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {"protocolVersion": "1"}},
        {"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {"protocolVersion": 77}},
        {"jsonrpc": "2.0", "id": 5, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 6, "method": "prompt", "params": {
            "session_id": "session_foreign", "prompt": "must not dispatch in v1",
        }},
        {"jsonrpc": "2.0", "id": 7, "method": "session/new", "params": {
            "cwd": str(workspace), "mcpServers": [], "additionalDirectories": [], "unsupported": True,
        }},
        {"jsonrpc": "2.0", "id": 8, "method": "session/new", "params": {
            "cwd": str(other), "mcpServers": [], "additionalDirectories": [],
        }},
        {"jsonrpc": "2.0", "id": 9, "method": "session/new", "params": {
            "cwd": str(workspace), "mcpServers": [{"name": "remote"}], "additionalDirectories": [],
        }},
        {"jsonrpc": "2.0", "id": 10, "method": "session/prompt", "params": {"sessionId": "session_foreign"}},
        {"jsonrpc": "2.0", "id": 11, "method": "session/new", "params": {
            "cwd": str(workspace), "mcpServers": [], "additionalDirectories": [],
        }},
        {"jsonrpc": "2.0", "id": 12, "method": "session/list", "params": {"cwd": str(other)}},
        {"jsonrpc": "2.0", "id": 13, "method": "session/list", "params": {"cwd": str(workspace)}},
    ]
    replies = await _raw_rpc(
        root=root, workspace=workspace, config=config, home=home, requests=requests,
        name="raw-v1-boundaries", evidence=evidence,
    )
    assert replies[0]["error"]["code"] == -32002
    assert replies[1]["error"]["code"] == -32602
    assert replies[2]["error"]["code"] == -32602
    assert replies[3]["result"]["protocolVersion"] == 1
    assert replies[4]["error"]["code"] == -32600
    assert replies[5]["error"]["code"] == -32601
    assert all(replies[index]["error"]["code"] == -32602 for index in (6, 7, 8))
    # session/prompt is implemented now; retain the foreign-session denial and
    # no-extra-session assertion instead of expecting the old unsupported method.
    assert replies[9]["error"]["code"] == -32001
    session_id = replies[10]["result"]["sessionId"]
    assert replies[11]["error"]["code"] == -32602
    assert [row["sessionId"] for row in replies[12]["result"]["sessions"]] == [session_id]
    assert len(list((workspace / ".harness" / "sessions").iterdir())) == 1


@pytest.mark.asyncio
async def test_v1_nul_cwds_return_errors_and_keep_connection_usable(tmp_path: Path):
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    evidence = _evidence_dir(tmp_path)
    for directory in (workspace, config, home):
        directory.mkdir(parents=True, exist_ok=True)
    invalid_cwd = f"{workspace}/\0invalid"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {
            "cwd": invalid_cwd, "mcpServers": [], "additionalDirectories": [],
        }},
        {"jsonrpc": "2.0", "id": 3, "method": "session/new", "params": {
            "cwd": str(workspace), "mcpServers": [], "additionalDirectories": [],
        }},
        {"jsonrpc": "2.0", "id": 4, "method": "session/list", "params": {"cwd": invalid_cwd}},
        {"jsonrpc": "2.0", "id": 5, "method": "session/list", "params": {"cwd": str(workspace)}},
    ]
    replies = await _raw_rpc(
        root=root, workspace=workspace, config=config, home=home, requests=requests,
        name="raw-nul-cwd", evidence=evidence,
    )
    assert replies[0]["result"]["protocolVersion"] == 1
    assert replies[1]["error"]["code"] == -32602
    session_id = replies[2]["result"]["sessionId"]
    assert replies[3]["error"]["code"] == -32602
    assert [row["sessionId"] for row in replies[4]["result"]["sessions"]] == [session_id]
    assert len(list((workspace / ".harness" / "sessions").iterdir())) == 1


@pytest.mark.asyncio
async def test_legacy_no_initialize_session_flow_and_dialect_lock_remain(tmp_path: Path):
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    evidence = _evidence_dir(tmp_path)
    for directory in (workspace, config, home):
        directory.mkdir(parents=True, exist_ok=True)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "session/prompt", "params": {"sessionId": "session_foreign"}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "session/new"},
        {"jsonrpc": "2.0", "id": 4, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 5, "method": "initialize", "params": {"protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 6, "method": "session/list", "params": {"cwd": str(workspace)}},
    ]
    replies = await _raw_rpc(
        root=root, workspace=workspace, config=config, home=home, requests=requests,
        name="raw-legacy-boundaries", evidence=evidence,
    )
    assert replies[0]["error"]["code"] == -32002
    assert replies[1]["result"] == []
    session_id = replies[2]["result"]["session_id"]
    assert replies[3]["result"]["protocolVersion"] == "0.1"
    assert replies[4]["error"]["code"] == -32600
    assert replies[5]["error"]["code"] == -32600
    assert (workspace / ".harness" / "sessions" / session_id).is_dir()


@pytest.mark.asyncio
async def test_official_client_prompt_accepts_text_and_resource_links_without_fetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The actual SDK client submits typed text and resource_link blocks through the CLI."""
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    evidence = _evidence_dir(tmp_path)
    outside = tmp_path / "outside-canary.txt"
    for directory in (workspace, config, home, evidence):
        directory.mkdir(parents=True, exist_ok=True)
    outside.write_text("OUTSIDE_RESOURCE_BYTES_MUST_NOT_BE_READ", encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
    }), encoding="utf-8")
    for key in tuple(os.environ):
        upper = key.upper()
        if any(marker in upper for marker in (
            "API_KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL", "PRIVATE_KEY", "ACCESS_KEY",
            "HERMES", "IMPORT_OVERRIDE",
        )):
            monkeypatch.delenv(key)
    monkeypatch.delenv("HARNESS_MOCK_SCRIPT", raising=False)
    child_probe = tmp_path / "child-provenance.json"
    child_env = _child_environment(root, config, home, tmp_path / "child-site", child_probe)
    assert "HARNESS_MOCK_SCRIPT" not in child_env

    class ClientCallbacks:
        def __init__(self) -> None:
            self.updates: list[tuple[str, object]] = []
            self.forbidden_calls: list[str] = []

        async def session_update(self, session_id: str, update: object, **_kwargs: object) -> None:
            self.updates.append((session_id, update))

        async def fs_read_text_file(self, *_args: object, **_kwargs: object) -> None:
            self.forbidden_calls.append("fs_read_text_file")
            raise AssertionError("resource_link must not trigger an SDK filesystem callback")

        async def terminal_create(self, *_args: object, **_kwargs: object) -> None:
            self.forbidden_calls.append("terminal_create")
            raise AssertionError("ACP v1 must not trigger an SDK terminal callback")

    callbacks = ClientCallbacks()
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        observed.append({
            "direction": getattr(getattr(event, "direction", None), "value", None),
            "message": getattr(event, "message", None),
        })

    child: object | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    stderr_text = ""
    try:
        async with asyncio.timeout(30):
            async with spawn_agent_process(
                callbacks,
                sys.executable,
                "-m",
                "harness.cli",
                "acp",
                "--workspace",
                str(workspace),
                cwd=root,
                env=child_env,
                transport_kwargs={"shutdown_timeout": 1.0},
                observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                initialized = await connection.initialize(protocol_version=1)
                assert initialized.protocol_version == 1
                session = await connection.new_session(
                    cwd=str(workspace), additional_directories=[], mcp_servers=[],
                )
                response = await connection.prompt(
                    session_id=session.session_id,
                    prompt=[
                        TextContentBlock(type="text", text="plain prompt text"),
                        ResourceContentBlock(
                            type="resource_link",
                            name="reference note",
                            uri=outside.as_uri(),
                            description="reference description",
                        ),
                    ],
                )
                assert response.stop_reason == "end_turn"
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        (evidence / "sdk-prompt-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (evidence / "sdk-prompt-stderr.log").write_text(stderr_text, encoding="utf-8")
        (evidence / "sdk-prompt-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
        }, indent=2) + "\n")
    assert getattr(child, "returncode", None) == 0, stderr_text[-4096:]
    _assert_child_provenance(child_probe, root, evidence, "sdk-prompt")
    assert callbacks.forbidden_calls == []
    chunks = [
        update.content.text
        for _, update in callbacks.updates
        if getattr(update, "session_update", None) == "agent_message_chunk"
    ]
    streamed = "".join(chunks)
    assert "plain prompt text" in streamed
    assert "reference note" in streamed
    assert outside.as_uri() in streamed
    assert "reference description" in streamed
    assert "OUTSIDE_RESOURCE_BYTES_MUST_NOT_BE_READ" not in streamed
    assert all(session_id == session.session_id for session_id, _ in callbacks.updates)


@pytest.mark.asyncio
async def test_official_client_receives_correlated_read_tool_updates(tmp_path: Path):
    """A real view_file call is represented with the SDK's typed tool-call updates."""
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    evidence = _evidence_dir(tmp_path)
    for directory in (workspace, config, home, evidence):
        directory.mkdir(parents=True, exist_ok=True)
    (workspace / "sample.txt").write_text("inside workspace text", encoding="utf-8")
    script = config / "mock-script.json"
    call_id = "t20b_read_call_17"
    script.write_text(json.dumps([
        {
            "thinking": "checking the workspace file",
            "tool_calls": [{"id": call_id, "name": "view_file", "arguments": {"file_path": "sample.txt"}}],
        },
        {"text": "read complete"},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")

    class ClientCallbacks:
        def __init__(self) -> None:
            self.updates: list[tuple[str, object]] = []

        async def session_update(self, session_id: str, update: object, **_kwargs: object) -> None:
            self.updates.append((session_id, update))

    callbacks = ClientCallbacks()
    child_probe = tmp_path / "child-provenance.json"
    child_env = _child_environment(root, config, home, tmp_path / "child-site", child_probe)
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        observed.append({
            "direction": getattr(getattr(event, "direction", None), "value", None),
            "message": getattr(event, "message", None),
        })

    child: object | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    stderr_text = ""
    try:
        async with asyncio.timeout(30):
            async with spawn_agent_process(
                callbacks,
                sys.executable,
                "-m",
                "harness.cli",
                "acp",
                "--workspace",
                str(workspace),
                cwd=root,
                env=child_env,
                transport_kwargs={"shutdown_timeout": 1.0},
                observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                assert (await connection.initialize(protocol_version=1)).protocol_version == 1
                session = await connection.new_session(
                    cwd=str(workspace), additional_directories=[], mcp_servers=[],
                )
                response = await connection.prompt(
                    session_id=session.session_id,
                    prompt=[TextContentBlock(type="text", text="read sample.txt")],
                )
                assert response.stop_reason == "end_turn"
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        (evidence / "sdk-tool-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (evidence / "sdk-tool-stderr.log").write_text(stderr_text, encoding="utf-8")
        (evidence / "sdk-tool-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
        }, indent=2) + "\n")
    assert getattr(child, "returncode", None) == 0, stderr_text[-4096:]
    _assert_child_provenance(child_probe, root, evidence, "sdk-tool")
    assert all(session_id == session.session_id for session_id, _ in callbacks.updates)
    thoughts = [
        update for _, update in callbacks.updates
        if getattr(update, "session_update", None) == "agent_thought_chunk"
    ]
    assert len(thoughts) == 1
    assert thoughts[0].content.text == "checking the workspace file"
    starts = [update for _, update in callbacks.updates if getattr(update, "session_update", None) == "tool_call"]
    results = [update for _, update in callbacks.updates if getattr(update, "session_update", None) == "tool_call_update"]
    assert len(starts) == 1
    assert starts[0].tool_call_id == call_id
    assert starts[0].title == "view_file"
    assert starts[0].kind == "read"
    assert starts[0].status == "pending"
    assert len(results) == 1
    assert results[0].tool_call_id == call_id
    assert results[0].status == "completed"
    assert "inside workspace text" in results[0].content[0].content.text

    transcript = workspace / ".harness" / "sessions" / session.session_id / "transcript.jsonl"
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assistant_index = next(index for index, record in enumerate(records) if record.get("kind") == "assistant.message")
    result_record = next(record for record in records if record.get("kind") == "tool.result")
    assert assistant_index < records.index(result_record)
    assert [result["tool_use_id"] for result in result_record["tool_results"]] == [call_id]

    messages = [row["message"] for row in observed if isinstance(row.get("message"), dict)]
    update_indices = [index for index, message in enumerate(messages) if message.get("method") == "session/update"]
    prompt_response_indices = [
        index for index, message in enumerate(messages)
        if message.get("id") is not None and isinstance(message.get("result"), dict)
        and "stopReason" in message["result"]
    ]
    assert len(update_indices) >= 3
    assert prompt_response_indices and max(update_indices) < prompt_response_indices[-1]


@pytest.mark.asyncio
async def test_official_client_write_tool_fails_closed_without_permission_rpc(tmp_path: Path):
    """A client without request_permission gets an RPC error and cannot authorize a write."""
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    evidence = _evidence_dir(tmp_path)
    for directory in (workspace, config, home, evidence):
        directory.mkdir(parents=True, exist_ok=True)
    marker = workspace / "must-not-be-created.txt"
    call_id = "t20b_write_call_19"
    script = config / "mock-script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{
            "id": call_id,
            "name": "write_to_file",
            "arguments": {"file_path": marker.name, "content": "unapproved write"},
        }]},
        {"text": "write attempt ended"},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")

    class ClientCallbacks:
        def __init__(self) -> None:
            self.updates: list[tuple[str, object]] = []

        async def session_update(self, session_id: str, update: object, **_kwargs: object) -> None:
            self.updates.append((session_id, update))

    callbacks = ClientCallbacks()
    child_probe = tmp_path / "child-provenance.json"
    child_env = _child_environment(root, config, home, tmp_path / "child-site", child_probe)
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        observed.append({
            "direction": getattr(getattr(event, "direction", None), "value", None),
            "message": getattr(event, "message", None),
        })

    child: object | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    stderr_text = ""
    try:
        async with asyncio.timeout(30):
            async with spawn_agent_process(
                callbacks,
                sys.executable,
                "-m",
                "harness.cli",
                "acp",
                "--workspace",
                str(workspace),
                cwd=root,
                env=child_env,
                transport_kwargs={"shutdown_timeout": 1.0},
                observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                assert (await connection.initialize(protocol_version=1)).protocol_version == 1
                session = await connection.new_session(
                    cwd=str(workspace), additional_directories=[], mcp_servers=[],
                )
                response = await connection.prompt(
                    session_id=session.session_id,
                    prompt=[TextContentBlock(type="text", text="try the guarded write")],
                )
                assert response.stop_reason == "end_turn"
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        (evidence / "sdk-write-denied-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (evidence / "sdk-write-denied-stderr.log").write_text(stderr_text, encoding="utf-8")
        (evidence / "sdk-write-denied-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
        }, indent=2) + "\n")
    assert getattr(child, "returncode", None) == 0, stderr_text[-4096:]
    _assert_child_provenance(child_probe, root, evidence, "sdk-write-denied")
    assert not marker.exists()
    terminal_updates = [
        update for _, update in callbacks.updates
        if getattr(update, "session_update", None) == "tool_call_update"
    ]
    assert len(terminal_updates) == 1
    assert terminal_updates[0].tool_call_id == call_id
    assert terminal_updates[0].status == "failed"
    messages = [row["message"] for row in observed if isinstance(row.get("message"), dict)]
    permission_requests = [
        message for message in messages if message.get("method") == "session/request_permission"
    ]
    assert len(permission_requests) == 1
    permission_id = permission_requests[0]["id"]
    assert permission_requests[0]["params"]["toolCall"]["toolCallId"] == call_id
    assert permission_requests[0]["params"]["toolCall"]["status"] == "pending"
    permission_errors = [
        message for message in messages
        if message.get("id") == permission_id and "method" not in message and "error" in message
    ]
    assert len(permission_errors) == 1
    assert permission_errors[0]["error"]["code"] == -32601
    assert not any(message.get("method") == "permission/request" for message in messages)


@pytest.mark.asyncio
async def test_official_client_cancel_waits_for_turn_cleanup_and_terminal_event(tmp_path: Path):
    """The SDK cancellation notification does not complete its prompt before runner cleanup."""
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    evidence = _evidence_dir(tmp_path)
    plugin = config / "plugins" / "provider-controlled"
    for directory in (workspace, config, home, evidence, plugin):
        directory.mkdir(parents=True, exist_ok=True)
    started = config / "provider-started"
    release = config / "provider-release"
    (plugin / "harness_plugin.toml").write_text(
        "[plugin]\nname = 'provider-controlled'\nversion = '1.0.0'\napi_version = '1'\n"
        "summary = 'test-only controlled provider'\nprovides = ['provider']\ncapabilities = []\n"
        "entry = 'provider:ControlledPlugin'\n",
        encoding="utf-8",
    )
    (plugin / "provider.py").write_text(
        "from pathlib import Path\n"
        "import asyncio\n"
        "from harness.sdk.testing import ScriptedProvider\n"
        "from harness.sdk.types import Done, ModelInfo, TextDelta\n"
        f"STARTED = Path({str(started)!r})\nRELEASE = Path({str(release)!r})\n"
        "class ControlledProvider:\n"
        "    name = 't20b-controlled'\n"
        "    def __init__(self):\n"
        "        self.scripted = ScriptedProvider("
        "responses=[[TextDelta(text='before cancellation'), Done(stop_reason='end_turn')]], "
        "models=[ModelInfo(id='controlled-v1', display_name='Controlled Mock')])\n"
        "        self.caps = self.scripted.caps\n"
        "    def is_configured(self): return True\n"
        "    async def is_reachable(self): return None\n"
        "    async def list_models(self): return await self.scripted.list_models()\n"
        "    async def generate(self, request):\n"
        "        async for event in self.scripted.generate(request):\n"
        "            yield event\n"
        "            if isinstance(event, TextDelta):\n"
        "                STARTED.write_text('ready', encoding='utf-8')\n"
        "                while not RELEASE.exists(): await asyncio.sleep(0.01)\n"
        "    async def aclose(self): await self.scripted.aclose()\n"
        "class ControlledPlugin:\n"
        "    async def setup(self, ctx):\n"
        "        self.provider = ControlledProvider()\n"
        "        ctx.register_provider(self.provider)\n"
        "    async def teardown(self): await self.provider.aclose()\n",
        encoding="utf-8",
    )
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "t20b-controlled", "model": "controlled-v1"},
    }), encoding="utf-8")

    class ClientCallbacks:
        def __init__(self) -> None:
            self.updates: list[tuple[str, object]] = []

        async def session_update(self, session_id: str, update: object, **_kwargs: object) -> None:
            self.updates.append((session_id, update))

    callbacks = ClientCallbacks()
    child_probe = tmp_path / "child-provenance.json"
    child_env = _child_environment(root, config, home, tmp_path / "child-site", child_probe)
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        observed.append({
            "direction": getattr(getattr(event, "direction", None), "value", None),
            "message": getattr(event, "message", None),
        })

    child: object | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    stderr_text = ""
    prompt_task: asyncio.Task[object] | None = None
    try:
        async with asyncio.timeout(20):
            async with spawn_agent_process(
                callbacks,
                sys.executable,
                "-m",
                "harness.cli",
                "acp",
                "--workspace",
                str(workspace),
                cwd=root,
                env=child_env,
                transport_kwargs={"shutdown_timeout": 1.0},
                observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                assert (await connection.initialize(protocol_version=1)).protocol_version == 1
                session = await connection.new_session(
                    cwd=str(workspace), additional_directories=[], mcp_servers=[],
                )
                prompt_task = asyncio.create_task(connection.prompt(
                    session_id=session.session_id,
                    prompt=[TextContentBlock(type="text", text="wait for controlled provider")],
                ))
                async with asyncio.timeout(5):
                    while not started.exists():
                        await asyncio.sleep(0.01)
                await connection.cancel(session_id=session.session_id)
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(prompt_task), timeout=0.1)
                release.write_text("release", encoding="utf-8")
                response = await asyncio.wait_for(prompt_task, timeout=3)
                assert response.stop_reason == "cancelled"
                assert any(
                    getattr(update, "session_update", None) == "agent_message_chunk"
                    and update.content.text == "before cancellation"
                    for _, update in callbacks.updates
                )
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        if prompt_task is not None and not prompt_task.done():
            prompt_task.cancel()
            await asyncio.gather(prompt_task, return_exceptions=True)
        (evidence / "sdk-cancel-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (evidence / "sdk-cancel-stderr.log").write_text(stderr_text, encoding="utf-8")
        (evidence / "sdk-cancel-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
            "provider_started": started.exists(),
            "provider_released": release.exists(),
        }, indent=2) + "\n")
    assert getattr(child, "returncode", None) == 0, stderr_text[-4096:]
    _assert_child_provenance(child_probe, root, evidence, "sdk-cancel")
    messages = [row["message"] for row in observed if isinstance(row.get("message"), dict)]
    cancel_notifications = [message for message in messages if message.get("method") == "session/cancel"]
    assert len(cancel_notifications) == 1
    assert cancel_notifications[0].get("id") is None
    assert not any(
        message.get("method") == "session/cancel" and message.get("id") is not None for message in messages
    )
    update_indices = [index for index, message in enumerate(messages) if message.get("method") == "session/update"]
    response_indices = [
        index for index, message in enumerate(messages)
        if message.get("id") is not None and isinstance(message.get("result"), dict)
        and message["result"].get("stopReason") == "cancelled"
    ]
    assert len(update_indices) == 1
    assert response_indices and update_indices[0] < response_indices[-1]


@pytest.mark.asyncio
async def test_official_client_rejects_unsupported_and_malformed_prompt_content(tmp_path: Path):
    """Unsupported media and malformed links are denied before any model transcript is made."""
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    evidence = _evidence_dir(tmp_path)
    for directory in (workspace, config, home, evidence):
        directory.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
    }), encoding="utf-8")

    class ClientCallbacks:
        async def session_update(self, session_id: str, update: object, **_kwargs: object) -> None:
            raise AssertionError("invalid content unexpectedly produced a session update")

    child_probe = tmp_path / "child-provenance.json"
    child_env = _child_environment(root, config, home, tmp_path / "child-site", child_probe)
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        observed.append({
            "direction": getattr(getattr(event, "direction", None), "value", None),
            "message": getattr(event, "message", None),
        })

    child: object | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    stderr_text = ""
    try:
        async with asyncio.timeout(20):
            async with spawn_agent_process(
                ClientCallbacks(),
                sys.executable,
                "-m",
                "harness.cli",
                "acp",
                "--workspace",
                str(workspace),
                cwd=root,
                env=child_env,
                transport_kwargs={"shutdown_timeout": 1.0},
                observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                assert (await connection.initialize(protocol_version=1)).protocol_version == 1
                session = await connection.new_session(
                    cwd=str(workspace), additional_directories=[], mcp_servers=[],
                )
                unsupported_blocks = [
                    ImageContentBlock(type="image", data="AA==", mime_type="image/png"),
                    AudioContentBlock(type="audio", data="AA==", mime_type="audio/wav"),
                    EmbeddedResourceContentBlock(
                        type="resource",
                        resource=TextResourceContents(type="text", uri="file:///outside", text="embedded"),
                    ),
                ]
                for block in unsupported_blocks:
                    with pytest.raises(RequestError):
                        await connection.prompt(session_id=session.session_id, prompt=[block])
                with pytest.raises(RequestError):
                    await connection._conn.send_request("session/prompt", {
                        "sessionId": session.session_id,
                        "prompt": [{
                            "type": "resource_link",
                            "name": "bad size",
                            "uri": "file:///outside",
                            "size": "not-an-integer",
                        }],
                    })
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        (evidence / "sdk-invalid-content-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (evidence / "sdk-invalid-content-stderr.log").write_text(stderr_text, encoding="utf-8")
        (evidence / "sdk-invalid-content-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
        }, indent=2) + "\n")
    assert getattr(child, "returncode", None) == 0, stderr_text[-4096:]
    _assert_child_provenance(child_probe, root, evidence, "sdk-invalid-content")
    transcript = workspace / ".harness" / "sessions" / session.session_id / "transcript.jsonl"
    assert not transcript.exists()


@pytest.mark.asyncio
async def test_official_client_approves_one_write_through_correlated_permission_rpc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    for directory in (workspace, config, home):
        directory.mkdir(parents=True, exist_ok=True)
    marker = workspace / "approved-once.txt"
    call_id = "t20c_write_call_27"
    script = config / "mock-script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{
            "id": call_id,
            "name": "write_to_file",
            "arguments": {"file_path": marker.name, "content": "approved by the official client"},
        }]},
        {"text": "write completed"},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")

    for key in tuple(os.environ):
        upper = key.upper()
        if any(env_marker in upper for env_marker in (
            "API_KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL", "PRIVATE_KEY", "ACCESS_KEY",
            "HERMES", "IMPORT_OVERRIDE",
        )):
            monkeypatch.delenv(key)
    monkeypatch.delenv("HARNESS_MOCK_SCRIPT", raising=False)

    class ClientCallbacks:
        def __init__(self) -> None:
            self.updates: list[tuple[str, object]] = []
            self.permissions: list[tuple[str, object, list[object]]] = []

        async def session_update(self, session_id: str, update: object, **_kwargs: object) -> None:
            self.updates.append((session_id, update))

        async def request_permission(
            self, session_id: str, tool_call: object, options: list[object], **_kwargs: object,
        ) -> RequestPermissionResponse:
            self.permissions.append((session_id, tool_call, options))
            allow = [option for option in options if getattr(option, "kind", None) == "allow_once"]
            assert len(allow) == 1
            assert {getattr(option, "kind", None) for option in options} == {"allow_once", "reject_once"}
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=allow[0].option_id)
            )

    callbacks = ClientCallbacks()
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        observed.append({
            "direction": getattr(getattr(event, "direction", None), "value", None),
            "message": getattr(event, "message", None),
        })

    child: object | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    stderr_text = ""
    try:
        async with asyncio.timeout(20):
            async with spawn_agent_process(
                callbacks,
                sys.executable,
                "-m",
                "harness.cli",
                "acp",
                "--workspace",
                str(workspace),
                cwd=root,
                env={
                    "HOME": str(home),
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                    "PYTHONPATH": str(root),
                    "PYTHONUNBUFFERED": "1",
                    "HARNESS_CONFIG_DIR": str(config),
                },
                transport_kwargs={"shutdown_timeout": 1.0},
                observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                assert (await connection.initialize(protocol_version=1)).protocol_version == 1
                session = await connection.new_session(
                    cwd=str(workspace), additional_directories=[], mcp_servers=[],
                )
                response = await connection.prompt(
                    session_id=session.session_id,
                    prompt=[TextContentBlock(type="text", text="write the approved marker")],
                )
                assert response.stop_reason == "end_turn"
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        if stderr_task is not None and not stderr_task.done():
            try:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                pass
        (tmp_path / "permission-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (tmp_path / "permission-stderr.log").write_text(stderr_text, encoding="utf-8")
        (tmp_path / "permission-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
        }, indent=2) + "\n", encoding="utf-8")

    assert getattr(child, "returncode", None) == 0, stderr_text[-4096:]
    assert marker.read_text(encoding="utf-8") == "approved by the official client"
    assert len(callbacks.permissions) == 1
    permission_session, tool_call, options = callbacks.permissions[0]
    assert permission_session == session.session_id
    assert tool_call.tool_call_id == call_id
    assert tool_call.status == "pending"
    assert len(options) == 2
    assert not any(getattr(option, "kind", None) in {"allow_always", "reject_always"} for option in options)
    updates = [update for update_session, update in callbacks.updates if update_session == session.session_id]
    starts = [update for update in updates if getattr(update, "session_update", None) == "tool_call"]
    finishes = [update for update in updates if getattr(update, "session_update", None) == "tool_call_update"]
    assert len(starts) == len(finishes) == 1
    assert starts[0].tool_call_id == finishes[0].tool_call_id == call_id
    assert starts[0].status == "pending"
    assert finishes[0].status == "completed"

    messages = [row["message"] for row in observed if isinstance(row.get("message"), dict)]
    requests = [message for message in messages if message.get("method") == "session/request_permission"]
    assert len(requests) == 1
    rpc_id = requests[0].get("id")
    assert isinstance(rpc_id, (str, int)) and not isinstance(rpc_id, bool)
    params = requests[0]["params"]
    assert params["sessionId"] == session.session_id
    assert params["toolCall"]["toolCallId"] == call_id
    assert params["toolCall"]["status"] == "pending"
    assert {option["kind"] for option in params["options"]} == {"allow_once", "reject_once"}
    client_response = [message for message in messages if message.get("id") == rpc_id and "method" not in message]
    assert len(client_response) == 1
    assert client_response[0]["result"] == {"outcome": {"outcome": "selected", "optionId": "allow_once"}}

    transcript_path = workspace / ".harness" / "sessions" / session.session_id / "transcript.jsonl"
    records = [json.loads(line) for line in transcript_path.read_text(encoding="utf-8").splitlines()]
    requested = [record for record in records if record.get("kind") == "approval.requested"]
    resolved = [record for record in records if record.get("kind") == "approval.resolved"]
    tool_results = [record for record in records if record.get("kind") == "tool.result"]
    assert len(requested) == len(resolved) == 1
    assert requested[0]["metadata"]["approval_id"] == resolved[0]["metadata"]["approval_id"]
    assert requested[0]["metadata"]["call_id"] == resolved[0]["metadata"]["call_id"] == call_id
    assert resolved[0]["metadata"]["approved"] is True
    assert any(call_id in json.dumps(record, sort_keys=True) for record in tool_results)
    update_positions = [index for index, message in enumerate(messages) if message.get("method") == "session/update"]
    permission_position = messages.index(requests[0])
    response_position = next(
        index for index, message in enumerate(messages)
        if message.get("id") is not None and isinstance(message.get("result"), dict)
        and message["result"].get("stopReason") == "end_turn"
    )
    assert update_positions and max(update_positions) < response_position
    assert permission_position < response_position


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["reject_once", "cancelled"])
async def test_official_client_rejection_never_starts_the_write_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str,
) -> None:
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    for directory in (workspace, config, home):
        directory.mkdir(parents=True, exist_ok=True)
    marker = workspace / "must-not-be-created.txt"
    call_id = "t20c_rejected_write_31"
    script = config / "mock-script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{
            "id": call_id,
            "name": "write_to_file",
            "arguments": {"file_path": marker.name, "content": "must remain denied"},
        }]},
        {"text": "write was rejected"},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")
    for key in tuple(os.environ):
        upper = key.upper()
        if any(env_marker in upper for env_marker in (
            "API_KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL", "PRIVATE_KEY", "ACCESS_KEY",
            "HERMES", "IMPORT_OVERRIDE",
        )):
            monkeypatch.delenv(key)
    monkeypatch.delenv("HARNESS_MOCK_SCRIPT", raising=False)

    class ClientCallbacks:
        def __init__(self) -> None:
            self.updates: list[tuple[str, object]] = []
            self.permissions: list[tuple[str, object, list[object]]] = []

        async def session_update(self, session_id: str, update: object, **_kwargs: object) -> None:
            self.updates.append((session_id, update))

        async def request_permission(
            self, session_id: str, tool_call: object, options: list[object], **_kwargs: object,
        ) -> RequestPermissionResponse:
            self.permissions.append((session_id, tool_call, options))
            if decision == "cancelled":
                return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
            reject = [option for option in options if getattr(option, "kind", None) == "reject_once"]
            assert len(reject) == 1
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=reject[0].option_id)
            )

    callbacks = ClientCallbacks()
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        observed.append({
            "direction": getattr(getattr(event, "direction", None), "value", None),
            "message": getattr(event, "message", None),
        })

    child: object | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    stderr_text = ""
    try:
        async with asyncio.timeout(20):
            async with spawn_agent_process(
                callbacks,
                sys.executable,
                "-m",
                "harness.cli",
                "acp",
                "--workspace",
                str(workspace),
                cwd=root,
                env={
                    "HOME": str(home),
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                    "PYTHONPATH": str(root),
                    "PYTHONUNBUFFERED": "1",
                    "HARNESS_CONFIG_DIR": str(config),
                },
                transport_kwargs={"shutdown_timeout": 1.0},
                observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                assert (await connection.initialize(protocol_version=1)).protocol_version == 1
                session = await connection.new_session(
                    cwd=str(workspace), additional_directories=[], mcp_servers=[],
                )
                response = await connection.prompt(
                    session_id=session.session_id,
                    prompt=[TextContentBlock(type="text", text="try the gated write")],
                )
                assert response.stop_reason == "end_turn"
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        if stderr_task is not None and not stderr_task.done():
            try:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                pass
        (tmp_path / "rejection-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (tmp_path / "rejection-stderr.log").write_text(stderr_text, encoding="utf-8")
        (tmp_path / "rejection-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
        }, indent=2) + "\n", encoding="utf-8")

    assert getattr(child, "returncode", None) == 0, stderr_text[-4096:]
    assert len(callbacks.permissions) == 1
    permission_session, tool_call, options = callbacks.permissions[0]
    assert permission_session == session.session_id
    assert tool_call.tool_call_id == call_id
    assert tool_call.status == "pending"
    assert {option.kind for option in options} == {"allow_once", "reject_once"}
    assert not marker.exists()
    tool_results = [
        update for update_session, update in callbacks.updates
        if update_session == session.session_id
        and getattr(update, "session_update", None) == "tool_call_update"
    ]
    assert len(tool_results) == 1
    assert tool_results[0].tool_call_id == call_id
    assert tool_results[0].status == "failed"
    messages = [row["message"] for row in observed if isinstance(row.get("message"), dict)]
    permission_requests = [message for message in messages if message.get("method") == "session/request_permission"]
    assert len(permission_requests) == 1
    permission_result = [
        message for message in messages
        if message.get("id") == permission_requests[0]["id"] and "method" not in message
    ]
    assert len(permission_result) == 1
    expected_outcome = (
        {"outcome": "cancelled"}
        if decision == "cancelled"
        else {"outcome": "selected", "optionId": "reject_once"}
    )
    assert permission_result[0]["result"]["outcome"] == expected_outcome
    transcript_path = workspace / ".harness" / "sessions" / session.session_id / "transcript.jsonl"
    records = [json.loads(line) for line in transcript_path.read_text(encoding="utf-8").splitlines()]
    requested = [record for record in records if record.get("kind") == "approval.requested"]
    resolved = [record for record in records if record.get("kind") == "approval.resolved"]
    assert len(requested) == len(resolved) == 1
    assert requested[0]["metadata"]["approval_id"] == resolved[0]["metadata"]["approval_id"]
    assert resolved[0]["metadata"]["approved"] is False
    assert resolved[0]["metadata"]["by"] == "acp"


@pytest.mark.asyncio
async def test_permission_rpc_ignores_wrong_id_invalid_option_duplicate_and_late_approve(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    for directory in (workspace, config, home):
        directory.mkdir(parents=True, exist_ok=True)
    marker = workspace / "invalid-option-must-not-write.txt"
    call_id = "t20c_invalid_permission_call_39"
    script = config / "mock-script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{
            "id": call_id,
            "name": "write_to_file",
            "arguments": {"file_path": marker.name, "content": "not approved"},
        }]},
        {"text": "invalid permission response was denied"},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m", "harness.cli", "acp", "--workspace", str(workspace),
        cwd=root,
        env={
            "HOME": str(home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(root),
            "PYTHONUNBUFFERED": "1",
            "HARNESS_CONFIG_DIR": str(config),
        },
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    stderr_task = asyncio.create_task(process.stderr.read())
    observed: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        observed.append({"direction": "outgoing", "message": message})
        process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        await process.stdin.drain()

    async def receive() -> dict[str, object]:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=3)
        assert line, "ACP child exited before the expected RPC response"
        message = json.loads(line)
        assert isinstance(message, dict)
        observed.append({"direction": "incoming", "message": message})
        return message

    stdout_tail = b""
    stderr_text = ""
    try:
        async with asyncio.timeout(15):
            await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}})
            assert (await receive())["result"]["protocolVersion"] == 1
            await send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {
                "cwd": str(workspace), "mcpServers": [], "additionalDirectories": [],
            }})
            session = (await receive())["result"]
            session_id = session["sessionId"]
            await send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt", "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "try the gated write"}],
            }})
            permission_request = None
            while permission_request is None:
                message = await receive()
                if message.get("method") == "session/request_permission":
                    permission_request = message
            rpc_id = permission_request["id"]
            assert permission_request["params"]["toolCall"]["toolCallId"] == call_id
            await send({"jsonrpc": "2.0", "id": "foreign-client-id", "result": {
                "outcome": {"outcome": "selected", "optionId": "allow_once"},
            }})
            await send({"jsonrpc": "2.0", "id": True, "result": {
                "outcome": {"outcome": "selected", "optionId": "allow_once"},
            }})
            await asyncio.sleep(0.1)
            assert not marker.exists()
            await send({"jsonrpc": "2.0", "id": rpc_id, "result": {
                "outcome": {"outcome": "selected", "optionId": "allow_always"},
            }})
            prompt_response = None
            while prompt_response is None:
                message = await receive()
                if message.get("id") == 3 and isinstance(message.get("result"), dict):
                    prompt_response = message
            assert prompt_response["result"]["stopReason"] == "end_turn"
            await send({"jsonrpc": "2.0", "id": rpc_id, "result": {
                "outcome": {"outcome": "selected", "optionId": "allow_once"},
            }})
            assert not marker.exists()
            process.stdin.close()
            await process.stdin.wait_closed()
            _, stdout_tail, stderr_raw = await asyncio.gather(
                process.wait(), process.stdout.read(), stderr_task,
            )
            stderr_text = stderr_raw.decode("utf-8", errors="replace")
    finally:
        if not process.stdin.is_closing():
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
        if not stderr_task.done():
            stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                "utf-8", errors="replace"
            )
        (tmp_path / "malformed-permission-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (tmp_path / "malformed-permission-stderr.log").write_text(stderr_text, encoding="utf-8")
        (tmp_path / "malformed-permission-cleanup.json").write_text(json.dumps({
            "pid": process.pid,
            "returncode": process.returncode,
            "stderr_bytes": len(stderr_text.encode("utf-8")),
            "stdout_tail_bytes": len(stdout_tail),
        }, indent=2) + "\n", encoding="utf-8")

    assert process.returncode == 0, stderr_text[-4096:]
    assert not stdout_tail.strip()
    assert not marker.exists()
    assert len([row for row in observed if row["direction"] == "incoming"
                and row["message"].get("method") == "session/request_permission"]) == 1
    transcript_path = workspace / ".harness" / "sessions" / session_id / "transcript.jsonl"
    records = [json.loads(line) for line in transcript_path.read_text(encoding="utf-8").splitlines()]
    resolved = [record for record in records if record.get("kind") == "approval.resolved"]
    assert len(resolved) == 1
    assert resolved[0]["metadata"]["call_id"] == call_id
    assert resolved[0]["metadata"]["approved"] is False


@pytest.mark.asyncio
async def test_cancel_while_permission_waits_denies_before_late_official_approve(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    for directory in (workspace, config, home):
        directory.mkdir(parents=True, exist_ok=True)
    marker = workspace / "cancelled-write-must-not-exist.txt"
    call_id = "t20c_cancelled_write_44"
    script = config / "mock-script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{
            "id": call_id,
            "name": "write_to_file",
            "arguments": {"file_path": marker.name, "content": "late approval must not write"},
        }]},
        {"text": "cancelled write was denied"},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")
    permission_started = asyncio.Event()
    release_permission = asyncio.Event()
    permission_returned = asyncio.Event()

    class ClientCallbacks:
        def __init__(self) -> None:
            self.updates: list[tuple[str, object]] = []

        async def session_update(self, session_id: str, update: object, **_kwargs: object) -> None:
            self.updates.append((session_id, update))

        async def request_permission(
            self, session_id: str, tool_call: object, options: list[object], **_kwargs: object,
        ) -> RequestPermissionResponse:
            permission_started.set()
            await release_permission.wait()
            permission_returned.set()
            allow = next(option for option in options if getattr(option, "kind", None) == "allow_once")
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=allow.option_id)
            )

    callbacks = ClientCallbacks()
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        observed.append({
            "direction": getattr(getattr(event, "direction", None), "value", None),
            "message": getattr(event, "message", None),
        })

    child: object | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    prompt_task: asyncio.Task[object] | None = None
    stderr_text = ""
    try:
        async with asyncio.timeout(15):
            async with spawn_agent_process(
                callbacks, sys.executable, "-m", "harness.cli", "acp",
                "--workspace", str(workspace), cwd=root,
                env={
                    "HOME": str(home),
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                    "PYTHONPATH": str(root),
                    "PYTHONUNBUFFERED": "1",
                    "HARNESS_CONFIG_DIR": str(config),
                },
                transport_kwargs={"shutdown_timeout": 1.0}, observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                assert (await connection.initialize(protocol_version=1)).protocol_version == 1
                session = await connection.new_session(
                    cwd=str(workspace), additional_directories=[], mcp_servers=[],
                )
                prompt_task = asyncio.create_task(connection.prompt(
                    session_id=session.session_id,
                    prompt=[TextContentBlock(type="text", text="wait for write approval")],
                ))
                await asyncio.wait_for(permission_started.wait(), timeout=5)
                cancel_started = time.monotonic()
                await connection.cancel(session_id=session.session_id)
                response = await asyncio.wait_for(prompt_task, timeout=0.95)
                assert time.monotonic() - cancel_started < 1.0
                assert response.stop_reason == "cancelled"
                assert not marker.exists()
                release_permission.set()
                await asyncio.wait_for(permission_returned.wait(), timeout=1)
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        release_permission.set()
        if prompt_task is not None and not prompt_task.done():
            prompt_task.cancel()
            await asyncio.gather(prompt_task, return_exceptions=True)
        (tmp_path / "cancel-permission-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (tmp_path / "cancel-permission-stderr.log").write_text(stderr_text, encoding="utf-8")
        (tmp_path / "cancel-permission-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
            "permission_started": permission_started.is_set(),
            "permission_returned": permission_returned.is_set(),
        }, indent=2) + "\n", encoding="utf-8")

    assert getattr(child, "returncode", None) == 0, stderr_text[-4096:]
    assert not marker.exists()
    messages = [row["message"] for row in observed if isinstance(row.get("message"), dict)]
    requests = [message for message in messages if message.get("method") == "session/request_permission"]
    assert len(requests) == 1
    response = [
        message for message in messages
        if message.get("id") == requests[0]["id"] and "method" not in message and "result" in message
    ]
    assert response == [{
        "jsonrpc": "2.0",
        "id": requests[0]["id"],
        "result": {"outcome": {"outcome": "selected", "optionId": "allow_once"}},
    }]
    prompt_responses = [
        message for message in messages
        if message.get("id") is not None and isinstance(message.get("result"), dict)
        and message["result"].get("stopReason") == "cancelled"
    ]
    assert len(prompt_responses) == 1
    assert messages.index(prompt_responses[0]) < messages.index(response[0])


@pytest.mark.asyncio
async def test_timed_out_permission_cannot_be_approved_by_a_late_official_response(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    for directory in (workspace, config, home):
        directory.mkdir(parents=True, exist_ok=True)
    marker = workspace / "timed-out-write-must-not-exist.txt"
    call_id = "t20c_timed_out_write_46"
    script = config / "mock-script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{
            "id": call_id,
            "name": "write_to_file",
            "arguments": {"file_path": marker.name, "content": "late approval must not write"},
        }]},
        {"text": "timed-out write was denied"},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {
            "provider": "mock",
            "model": "mock-v1",
            "security": {"approval_timeout_s": 0.15},
        },
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")
    permission_started = asyncio.Event()
    release_permission = asyncio.Event()
    permission_returned = asyncio.Event()

    class ClientCallbacks:
        def __init__(self) -> None:
            self.updates: list[tuple[str, object]] = []

        async def session_update(self, session_id: str, update: object, **_kwargs: object) -> None:
            self.updates.append((session_id, update))

        async def request_permission(
            self, session_id: str, tool_call: object, options: list[object], **_kwargs: object,
        ) -> RequestPermissionResponse:
            permission_started.set()
            await release_permission.wait()
            permission_returned.set()
            allow = next(option for option in options if getattr(option, "kind", None) == "allow_once")
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=allow.option_id)
            )

    callbacks = ClientCallbacks()
    observed: list[dict[str, object]] = []

    def observe(event: object) -> None:
        observed.append({
            "direction": getattr(getattr(event, "direction", None), "value", None),
            "message": getattr(event, "message", None),
        })

    child: object | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    prompt_task: asyncio.Task[object] | None = None
    stderr_text = ""
    try:
        async with asyncio.timeout(15):
            async with spawn_agent_process(
                callbacks, sys.executable, "-m", "harness.cli", "acp",
                "--workspace", str(workspace), cwd=root,
                env={
                    "HOME": str(home),
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                    "PYTHONPATH": str(root),
                    "PYTHONUNBUFFERED": "1",
                    "HARNESS_CONFIG_DIR": str(config),
                },
                transport_kwargs={"shutdown_timeout": 1.0}, observers=[observe],
            ) as (connection, process):
                child = process
                assert process.stderr is not None
                stderr_task = asyncio.create_task(process.stderr.read())
                assert (await connection.initialize(protocol_version=1)).protocol_version == 1
                session = await connection.new_session(
                    cwd=str(workspace), additional_directories=[], mcp_servers=[],
                )
                prompt_task = asyncio.create_task(connection.prompt(
                    session_id=session.session_id,
                    prompt=[TextContentBlock(type="text", text="wait for timed-out write approval")],
                ))
                await asyncio.wait_for(permission_started.wait(), timeout=5)
                response = await asyncio.wait_for(prompt_task, timeout=1.0)
                assert response.stop_reason == "end_turn"
                assert not marker.exists()
                release_permission.set()
                await asyncio.wait_for(permission_returned.wait(), timeout=1)
            if stderr_task is not None:
                stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                    "utf-8", errors="replace"
                )
    finally:
        release_permission.set()
        if prompt_task is not None and not prompt_task.done():
            prompt_task.cancel()
            await asyncio.gather(prompt_task, return_exceptions=True)
        (tmp_path / "timeout-permission-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (tmp_path / "timeout-permission-stderr.log").write_text(stderr_text, encoding="utf-8")
        (tmp_path / "timeout-permission-cleanup.json").write_text(json.dumps({
            "pid": getattr(child, "pid", None),
            "returncode": getattr(child, "returncode", None),
            "stderr_bytes": len(stderr_text.encode("utf-8")),
            "permission_started": permission_started.is_set(),
            "permission_returned": permission_returned.is_set(),
        }, indent=2) + "\n", encoding="utf-8")

    assert getattr(child, "returncode", None) == 0, stderr_text[-4096:]
    assert not marker.exists()
    messages = [row["message"] for row in observed if isinstance(row.get("message"), dict)]
    requests = [message for message in messages if message.get("method") == "session/request_permission"]
    assert len(requests) == 1
    late_approvals = [
        message for message in messages
        if message.get("id") == requests[0]["id"] and "method" not in message
        and message.get("result", {}).get("outcome", {}).get("optionId") == "allow_once"
    ]
    assert len(late_approvals) == 1
    tool_results = [
        update for update_session, update in callbacks.updates
        if update_session == session.session_id
        and getattr(update, "session_update", None) == "tool_call_update"
    ]
    assert len(tool_results) == 1
    assert tool_results[0].tool_call_id == call_id
    assert tool_results[0].status == "failed"
    transcript_path = workspace / ".harness" / "sessions" / session.session_id / "transcript.jsonl"
    records = [json.loads(line) for line in transcript_path.read_text(encoding="utf-8").splitlines()]
    requested = [record for record in records if record.get("kind") == "approval.requested"]
    resolved = [record for record in records if record.get("kind") == "approval.resolved"]
    assert len(requested) == len(resolved) == 1
    assert requested[0]["metadata"]["approval_id"] == resolved[0]["metadata"]["approval_id"]
    assert requested[0]["metadata"]["call_id"] == resolved[0]["metadata"]["call_id"] == call_id
    assert resolved[0]["metadata"]["approved"] is False


@pytest.mark.asyncio
async def test_eof_with_pending_permission_cancels_turn_and_cleans_up(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    workspace, config, home = (tmp_path / name for name in ("workspace", "config", "home"))
    for directory in (workspace, config, home):
        directory.mkdir(parents=True, exist_ok=True)
    marker = workspace / "eof-write-must-not-exist.txt"
    call_id = "t20c_eof_write_49"
    script = config / "mock-script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{
            "id": call_id,
            "name": "write_to_file",
            "arguments": {"file_path": marker.name, "content": "EOF must deny"},
        }]},
        {"text": "EOF cancelled the write"},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "harness.cli", "acp", "--workspace", str(workspace),
        cwd=root,
        env={
            "HOME": str(home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(root),
            "PYTHONUNBUFFERED": "1",
            "HARNESS_CONFIG_DIR": str(config),
        },
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    stderr_task = asyncio.create_task(process.stderr.read())
    observed: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        observed.append({"direction": "outgoing", "message": message})
        process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        await process.stdin.drain()

    async def receive() -> dict[str, object]:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=3)
        assert line, "ACP child exited before permission was requested"
        message = json.loads(line)
        assert isinstance(message, dict)
        observed.append({"direction": "incoming", "message": message})
        return message

    stdout_tail = b""
    stderr_text = ""
    try:
        async with asyncio.timeout(12):
            await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}})
            assert (await receive())["result"]["protocolVersion"] == 1
            await send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {
                "cwd": str(workspace), "mcpServers": [], "additionalDirectories": [],
            }})
            session_id = (await receive())["result"]["sessionId"]
            await send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt", "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "wait for permission then disconnect"}],
            }})
            permission_request = None
            while permission_request is None:
                message = await receive()
                if message.get("method") == "session/request_permission":
                    permission_request = message
            process.stdin.close()
            await process.stdin.wait_closed()
            _, stdout_tail, stderr_raw = await asyncio.gather(
                process.wait(), process.stdout.read(), stderr_task,
            )
            stderr_text = stderr_raw.decode("utf-8", errors="replace")
    finally:
        if not process.stdin.is_closing():
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
        if not stderr_task.done():
            stderr_text = (await asyncio.wait_for(stderr_task, timeout=2)).decode(
                "utf-8", errors="replace"
            )
        (tmp_path / "eof-permission-wire.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observed),
            encoding="utf-8",
        )
        (tmp_path / "eof-permission-stderr.log").write_text(stderr_text, encoding="utf-8")
        (tmp_path / "eof-permission-cleanup.json").write_text(json.dumps({
            "pid": process.pid,
            "returncode": process.returncode,
            "stderr_bytes": len(stderr_text.encode("utf-8")),
            "stdout_tail_bytes": len(stdout_tail),
        }, indent=2) + "\n", encoding="utf-8")

    assert process.returncode == 0, stderr_text[-4096:]
    assert not marker.exists()
    for line in stdout_tail.splitlines():
        if line.strip():
            message = json.loads(line)
            assert isinstance(message, dict)
            observed.append({"direction": "incoming", "message": message})
    messages = [row["message"] for row in observed if isinstance(row.get("message"), dict)]
    request_index = next(index for index, message in enumerate(messages)
                         if message.get("method") == "session/request_permission")
    prompt_responses = [
        (index, message) for index, message in enumerate(messages)
        if message.get("id") == 3 and isinstance(message.get("result"), dict)
        and "stopReason" in message["result"]
    ]
    assert len(prompt_responses) == 1
    assert prompt_responses[0][1]["result"]["stopReason"] == "cancelled"
    tool_updates = [
        (index, message) for index, message in enumerate(messages)
        if message.get("method") == "session/update"
        and message.get("params", {}).get("update", {}).get("sessionUpdate") == "tool_call_update"
    ]
    assert any(update["params"]["update"].get("toolCallId") == call_id
               and update["params"]["update"].get("status") == "failed"
               for _, update in tool_updates)
    assert all(index < prompt_responses[0][0] for index, _ in tool_updates)
    assert request_index < prompt_responses[0][0]


@pytest.mark.asyncio
async def test_v1_approval_receiver_does_not_make_real_ask_user_wait(tmp_path: Path) -> None:
    workspace, config = tmp_path / "workspace", tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    script = config / "mock-script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{
            "id": "t20c_question_call_04",
            "name": "ask_user",
            "arguments": {"questions": [{
                "id": "choice",
                "question": "Which option?",
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            }]},
        }]},
        {"text": "I proceeded with my best assumption."},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")
    kernel = await bootstrap_kernel(workspace=workspace, config_dir=config, resume_session=False)
    try:
        server = ACPServer(kernel.commands, bus=kernel.bus)
        server._connected = True
        server._dialect = "v1"
        response = await server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(workspace), "mcpServers": [], "additionalDirectories": []},
        })
        assert response is not None and "result" in response
        session_id = response["result"]["sessionId"]
        assert kernel.bus.has_interactive_subscriber(session_id, interaction_kind="approval")
        assert not kernel.bus.has_interactive_subscriber(session_id, interaction_kind="question")

        question_elapsed: list[float] = []
        interactions = kernel.commands.gate.approvals.interactions
        ask = interactions.ask

        async def measured_ask(kind: str, payload: dict[str, object], **kwargs: object) -> object:
            started_at = time.monotonic()
            try:
                return await ask(kind, payload, **kwargs)
            finally:
                if kind == "question":
                    question_elapsed.append(time.monotonic() - started_at)

        interactions.ask = measured_ask
        # Bound the whole offline turn; Q04 itself is measured only around ask_user's broker call.
        result = await asyncio.wait_for(
            kernel.commands.submit_turn(session_id=session_id, prompt="ask the user"),
            timeout=8.0,
        )
        assert len(question_elapsed) == 1
        assert question_elapsed[0] < 1.0
        assert result.stop_reason == "end_turn"
        assert await kernel.commands.interactions_list(session_id=session_id) == []
        transcript = workspace / ".harness" / "sessions" / session_id / "transcript.jsonl"
        records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
        assert not any(record.get("kind") == "question.asked" for record in records)
    finally:
        await kernel.registry.unload_all()


@pytest.mark.asyncio
async def test_v1_permission_forwarder_never_owns_or_resolves_foreign_session_approval(
    tmp_path: Path,
) -> None:
    workspace, config = tmp_path / "workspace", tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    marker = workspace / "foreign-write-must-not-exist.txt"
    script = config / "mock-script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{
            "id": "t20c_foreign_write_53",
            "name": "write_to_file",
            "arguments": {"file_path": marker.name, "content": "not owned by the ACP peer"},
        }]},
        {"text": "foreign write remains denied"},
    ]), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({
        "schema_version": 1,
        "kernel": {"provider": "mock", "model": "mock-v1"},
        "plugins": {"provider-mock": {"script_file": str(script)}},
    }), encoding="utf-8")
    kernel = await bootstrap_kernel(workspace=workspace, config_dir=config, resume_session=False)

    class Writer:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        def is_closing(self) -> bool:
            return False

        def write(self, data: bytes) -> None:
            message = json.loads(data)
            assert isinstance(message, dict)
            self.messages.append(message)

        async def drain(self) -> None:
            return None

    server_task: asyncio.Task[None] | None = None
    foreign_receiver = None
    foreign_turn: asyncio.Task[object] | None = None
    try:
        server = ACPServer(kernel.commands, bus=kernel.bus)
        server._connected = True
        server._dialect = "v1"
        server._writer = Writer()
        created = await server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/new",
            "params": {"cwd": str(workspace), "mcpServers": [], "additionalDirectories": []},
        })
        assert created is not None and "result" in created
        owned_session = created["result"]["sessionId"]
        assert kernel.bus.has_interactive_subscriber(owned_session, interaction_kind="approval")
        assert not kernel.bus.has_interactive_subscriber(owned_session, interaction_kind="question")
        foreign = await kernel.commands.sessions_create()
        foreign_session = foreign["session_id"]
        assert foreign_session not in server._owned_sessions
        foreign_receiver = kernel.bus.subscribe(
            types={"approval.required"}, interactive=True, max_queue_size=8,
            session_id=foreign_session, interaction_kinds={"approval"},
        )
        server_task = asyncio.create_task(server._forward_bus_events())
        await asyncio.wait_for(server._bus_ready.wait(), timeout=1)
        foreign_turn = asyncio.create_task(kernel.commands.submit_turn(
            session_id=foreign_session, prompt="try the foreign write",
        ))
        event = await asyncio.wait_for(foreign_receiver.get(), timeout=5)
        assert event.type == "approval.required"
        approval_id = event.data["approval_id"]
        assert kernel.commands.gate.approvals.peek(approval_id).session_id == foreign_session
        await asyncio.sleep(0.05)
        assert server._writer.messages == []
        assert server._v1_pending_permissions == {}
        assert kernel.commands.gate.approvals.peek(approval_id) is not None
        assert not marker.exists()

        await kernel.commands.resolve_approval(
            approval_id=approval_id, approved=False, by="test", reason="foreign control cleanup",
        )
        result = await asyncio.wait_for(foreign_turn, timeout=2)
        assert result.stop_reason == "end_turn"
        assert not marker.exists()
        assert server._writer.messages == []
    finally:
        if foreign_turn is not None and not foreign_turn.done():
            foreign_turn.cancel()
            await asyncio.gather(foreign_turn, return_exceptions=True)
        if server_task is not None:
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
        if foreign_receiver is not None:
            kernel.bus.unsubscribe(foreign_receiver)
        await kernel.registry.unload_all()

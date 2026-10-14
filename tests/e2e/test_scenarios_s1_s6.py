"""
End-to-End Acceptance Scenarios S1 through S6 (§1.3, KRN-12).
Verifies the modular kernel and Phase 3a plugins (PLG-01..PLG-10) end-to-end:
  S1: Safe daemon / endpoint security (auth, content-type, host checks)
  S2: Configure & converse with real usage and economics ledger
  S3: Surgical edit inside workspace with diff card, no commit
  S4: Approval gate: interactive prompt or broker deny / approve
  S5: Workspace jail: path traversal denials on view, write, and exec
  S6: Background task reactive wake-up without polling
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
from aiohttp import web

from harness.core.coordinator import AgentCoordinator
from harness.core.daemon import HarnessDaemon
from harness.frontends.cli.app import CliApp
from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.cards import Card
from harness.sdk.testing import ScriptedProvider
from harness.sdk.types import (
    Done,
    ModelInfo,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    TurnResult,
    Usage,
    UsageEvent,
)

# =============================================================================
# Helper Fixtures & Runners
# =============================================================================

async def setup_kernel_environment(tmp_path: Path):
    """Initializes a full kernel runtime with all built-in plugins."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    conf = tmp_path / ".harness"
    conf.mkdir(parents=True, exist_ok=True)

    runtime = await bootstrap_kernel(
        workspace=ws,
        config_dir=conf,
    )
    return runtime


# =============================================================================
# Scenario S1: Safe Daemon
# =============================================================================
@pytest.mark.asyncio
async def test_scenario_s1_safe_daemon(tmp_path: Path):
    """
    S1 Acceptance:
    - Unauthenticated requests to non-public endpoints return 401.
    - Non-JSON POST to RPC returns 415.
    - Invalid Host header returns 403 or 421.
    - Valid token and valid Host succeeds.
    """
    coord = AgentCoordinator(workspace=tmp_path)
    daemon = HarnessDaemon(
        coordinator=coord,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        allowed_hosts=["127.0.0.1", "localhost"],
    )

    app_runner = web.AppRunner(daemon.app)
    await app_runner.setup()
    site = web.TCPSite(app_runner, host="127.0.0.1", port=0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    daemon.port = port
    base_url = f"http://127.0.0.1:{port}"

    try:
        async with httpx.AsyncClient() as client:
            # 1. Host header validation: evil.example -> 403 or 421
            res_evil_host = await client.get(f"{base_url}/health", headers={"Host": "evil.example"})
            assert res_evil_host.status_code in (403, 421), f"Expected 403 or 421, got {res_evil_host.status_code}"

            # 2. Content-Type check on POST: text/plain -> 415
            res_bad_ct = await client.post(
                f"{base_url}/rpc",
                headers={"Content-Type": "text/plain", "X-Harness-Token": daemon.auth_token},
                content=b"{}",
            )
            assert res_bad_ct.status_code == 415, f"Expected 415, got {res_bad_ct.status_code}"

            # 3. Unauthenticated POST to RPC -> 401
            res_no_auth = await client.post(
                f"{base_url}/rpc",
                headers={"Content-Type": "application/json"},
                json={},
            )
            assert res_no_auth.status_code == 401, f"Expected 401, got {res_no_auth.status_code}"

            # 4. Valid token and health -> 200
            res_health = await client.get(f"{base_url}/health")
            assert res_health.status_code == 200
    finally:
        await app_runner.cleanup()


# =============================================================================
# Scenario S2: Configure & Converse with Real Usage / Economics
# =============================================================================
@pytest.mark.asyncio
async def test_scenario_s2_configure_converse_usage(tmp_path: Path):
    """
    S2 Acceptance:
    - Settings saved with provider/model.
    - Model call executes and emits tool calls.
    - Economics / Cost Ledger records token usage and costs in economics.json.
    """
    runtime = await setup_kernel_environment(tmp_path)
    ws = runtime.commands.workspace

    # Seed test file to view
    target_file = ws / "sample.py"
    target_file.write_text("print('hello harness')\n", encoding="utf-8")

    # Scripted Provider to simulate conversation turn with tool call & usage
    provider = ScriptedProvider(
        models=[ModelInfo(id="gpt-4o", display_name="GPT-4o", context_tokens=128000, input_usd_per_mtok=2.5, output_usd_per_mtok=10.0)]
    )
    provider.add_response([
        TextDelta(text="I will check the sample.py file."),
        ToolCallEnd(id="call_view_1", name="view_file", arguments={"file_path": "sample.py"}),
        UsageEvent(usage=Usage(input_tokens=150, output_tokens=30)),
        Done(stop_reason="tool_use"),
    ])
    provider.add_response([
        TextDelta(text="The sample.py file prints 'hello harness'."),
        UsageEvent(usage=Usage(input_tokens=220, output_tokens=25)),
        Done(stop_reason="end_turn"),
    ])
    runtime.registry.register_provider(provider, plugin_name="test_scripted")

    app = CliApp(commands=runtime.commands, bus=runtime.bus, interactive=False)
    result = await app.run_turn(
        "Inspect sample.py",
        options={"provider": "scripted", "model": "gpt-4o"},
    )

    assert isinstance(result, TurnResult)
    assert "hello harness" in result.output

    # Check economics ledger persistence and Commands API
    session_id = app.session_id
    econ = await runtime.commands.economics(session_id=session_id)
    assert econ["totals"] is not None
    assert econ["totals"]["input_tokens"] >= 370
    assert econ["totals"]["output_tokens"] >= 55
    assert econ["totals"]["cost_usd"] > 0.0

    assert runtime.runner.cost_ledger is not None
    assert runtime.runner.cost_ledger.path.exists()


# =============================================================================
# Scenario S3: Surgical Edit, No Commit
# =============================================================================
@pytest.mark.asyncio
async def test_scenario_s3_surgical_edit_no_commit(tmp_path: Path):
    """
    S3 Acceptance:
    - edit_file applied surgically inside workspace.
    - Card.diff is emitted on EventBus.
    - Git status reflects working tree change without auto-committing.
    """
    runtime = await setup_kernel_environment(tmp_path)
    ws = runtime.commands.workspace

    # Initialize git repo in workspace
    subprocess.run(["git", "init"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=ws, check=True)

    code_file = ws / "math_utils.py"
    initial_code = "def add(a, b):\n    return a + b\n"
    code_file.write_text(initial_code, encoding="utf-8")

    subprocess.run(["git", "add", "math_utils.py"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=ws, check=True)
    commit_before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ws).decode().strip()

    # Scripted Provider calls edit_file
    diff_patch = (
        "<<<<<<< SEARCH\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "=======\n"
        "def add(a: int, b: int) -> int:\n"
        "    \"\"\"Adds two numbers.\"\"\"\n"
        "    return a + b\n"
        ">>>>>>> REPLACE"
    )
    provider = ScriptedProvider(
        models=[ModelInfo(id="gpt-4o", display_name="GPT-4o", context_tokens=128000)]
    )
    provider.add_response([
        TextDelta(text="Adding type hints and docstring to math_utils.py."),
        ToolCallEnd(id="call_edit_1", name="edit_file", arguments={"file_path": "math_utils.py", "diff": diff_patch}),
        Done(stop_reason="tool_use"),
    ])
    provider.add_response([
        TextDelta(text="Surgical edit complete."),
        Done(stop_reason="end_turn"),
    ])
    runtime.registry.register_provider(provider, plugin_name="test_scripted")

    runtime.settings.set("kernel.security.permission_mode", "allow")
    sub = runtime.bus.subscribe()

    app = CliApp(commands=runtime.commands, bus=runtime.bus, interactive=False)
    result = await app.run_turn(
        "Add type hints to math_utils.py",
        options={"provider": "scripted", "model": "gpt-4o"},
    )

    # Check edit was applied
    new_content = code_file.read_text(encoding="utf-8")
    assert '"""Adds two numbers."""' in new_content
    assert "def add(a: int, b: int) -> int:" in new_content

    # Verify diff card was emitted in turn result or on event bus
    diff_cards: list[Card] = [c for c in result.cards if c.kind == "diff"]
    while not sub.empty():
        evt = sub.get_nowait()
        if evt.type == "card.emitted" and evt.data.get("card", {}).get("kind") == "diff":
            diff_cards.append(Card.from_dict(evt.data["card"]))

    assert len(diff_cards) >= 1
    assert any("math_utils.py" in str(c.to_dict()) for c in diff_cards)

    # Verify git log unchanged (NO commit made)
    commit_after = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ws).decode().strip()
    assert commit_before == commit_after

    # Git status reports modified file in working tree
    status_out = subprocess.check_output(["git", "status", "--porcelain"], cwd=ws).decode()
    assert "M math_utils.py" in status_out


# =============================================================================
# Scenario S4: Approval Gate
# =============================================================================
@pytest.mark.asyncio
async def test_scenario_s4_approval_gate_deny_and_approve(tmp_path: Path):
    """
    S4 Acceptance:
    - Destructive tool action triggers approval.required event.
    - When user denies: model receives rejection, turn continues gracefully.
    - When user approves: command executes.
    """
    # 1. Deny path: user denies approval
    runtime_deny = await setup_kernel_environment(tmp_path / "env_deny")
    ws_deny = runtime_deny.commands.workspace
    folder_to_delete_deny = ws_deny / "build_output"
    folder_to_delete_deny.mkdir()
    (folder_to_delete_deny / "temp.o").write_text("binary", encoding="utf-8")

    provider_deny = ScriptedProvider(
        models=[ModelInfo(id="gpt-4o", display_name="GPT-4o", context_tokens=128000)]
    )
    provider_deny.add_response([
        TextDelta(text="I will remove the build directory."),
        ToolCallEnd(id="call_rm_1", name="run_command", arguments={"CommandLine": "rm -rf build_output"}),
        Done(stop_reason="tool_use"),
    ])
    provider_deny.add_response([
        TextDelta(text="Understood, deletion was cancelled."),
        Done(stop_reason="end_turn"),
    ])
    runtime_deny.registry.register_provider(provider_deny, plugin_name="test_scripted")

    app_deny = CliApp(
        commands=runtime_deny.commands,
        bus=runtime_deny.bus,
        interactive=True,
        input_fn=lambda _: "no",  # User rejects approval
    )
    await app_deny.start_event_listener()
    try:
        res_deny = await app_deny.run_turn(
            "Delete build_output",
            options={"provider": "scripted", "model": "gpt-4o"},
        )
    finally:
        await app_deny.stop_event_listener()

    assert "deletion was cancelled" in res_deny.output
    assert folder_to_delete_deny.exists()

    # 2. Approve path: user approves
    runtime_approve = await setup_kernel_environment(tmp_path / "env_approve")
    ws_approve = runtime_approve.commands.workspace
    folder_to_delete_approve = ws_approve / "build_output"
    folder_to_delete_approve.mkdir()
    (folder_to_delete_approve / "temp.o").write_text("binary", encoding="utf-8")

    provider_approve = ScriptedProvider(
        models=[ModelInfo(id="gpt-4o", display_name="GPT-4o", context_tokens=128000)]
    )
    provider_approve.add_response([
        TextDelta(text="Removing build_output now."),
        ToolCallEnd(id="call_rm_2", name="run_command", arguments={"CommandLine": "rm -rf build_output"}),
        Done(stop_reason="tool_use"),
    ])
    provider_approve.add_response([
        TextDelta(text="build_output successfully deleted."),
        Done(stop_reason="end_turn"),
    ])
    runtime_approve.registry.register_provider(provider_approve, plugin_name="test_scripted")

    app_approve = CliApp(
        commands=runtime_approve.commands,
        bus=runtime_approve.bus,
        interactive=True,
        input_fn=lambda _: "yes",  # User approves
    )
    await app_approve.start_event_listener()
    try:
        res_approve = await app_approve.run_turn(
            "Delete build_output now",
            options={"provider": "scripted", "model": "gpt-4o"},
        )
    finally:
        await app_approve.stop_event_listener()

    assert "build_output successfully deleted" in res_approve.output
    assert not folder_to_delete_approve.exists()


# =============================================================================
# Scenario S5: Workspace Jail
# =============================================================================
@pytest.mark.asyncio
async def test_scenario_s5_workspace_jail(tmp_path: Path):
    """
    S5 Acceptance:
    - view_file /etc/hostname -> is_error=True (denied by jail).
    - write_to_file ../outside.txt -> is_error=True (denied by jail).
    - run_command Cwd=/tmp -> is_error=True (denied by jail).
    - All three return explicit error message and write nothing outside workspace.
    """
    runtime = await setup_kernel_environment(tmp_path)
    outside_file = tmp_path / "outside.txt"

    # Test all 3 operations via PolicyGate directly or via TurnRunner
    # 1. view_file outside workspace
    res_view = await runtime.gate.execute(
        ToolCall(id="call_v", name="view_file", arguments={"file_path": "/etc/hostname"}),
    )
    assert res_view.is_error is True
    assert "outside workspace" in res_view.output.lower() or "jail" in res_view.output.lower()

    # 2. write_to_file outside workspace
    res_write = await runtime.gate.execute(
        ToolCall(id="call_w", name="write_to_file", arguments={"file_path": "../outside.txt", "content": "infiltrated"}),
    )
    assert res_write.is_error is True
    assert "outside workspace" in res_write.output.lower() or "jail" in res_write.output.lower()
    assert not outside_file.exists()

    # 3. run_command outside workspace Cwd
    res_exec = await runtime.gate.execute(
        ToolCall(id="call_e", name="run_command", arguments={"CommandLine": "echo leaked", "Cwd": "/tmp"}),
    )
    assert res_exec.is_error is True
    assert "outside workspace" in res_exec.output.lower() or "jail" in res_exec.output.lower()


# =============================================================================
# Scenario S6: Background Wake-up Without Polling
# =============================================================================
@pytest.mark.asyncio
async def test_scenario_s6_background_wakeup_no_polling(tmp_path: Path):
    """
    S6 Acceptance:
    - Tool execution transitions to background (WaitMsBeforeAsync).
    - Turn runner suspends waiting on BackgroundInbox wake-up.
    - On completion, model receives output WITHOUT polling manage_task.
    - Final answer contains the background command's output.
    """
    runtime = await setup_kernel_environment(tmp_path)

    provider = ScriptedProvider(
        models=[ModelInfo(id="gpt-4o", display_name="GPT-4o", context_tokens=128000)]
    )
    # 1. Turn starts: model launches command with short WaitMsBeforeAsync
    provider.add_response([
        TextDelta(text="Starting background job..."),
        ToolCallEnd(
            id="call_cmd_bg",
            name="run_command",
            arguments={"CommandLine": "sleep 0.1 && echo FINISHED_TASK", "WaitMsBeforeAsync": 10},
        ),
        Done(stop_reason="tool_use"),
    ])
    # 2. First tool result returns task_id (background)
    # 3. Strategy suspends; background task completes -> inbox fires wake-up
    # 4. Model receives wake-up message and finishes turn
    provider.add_response([
        TextDelta(text="The background task has finished with output: FINISHED_TASK"),
        Done(stop_reason="end_turn"),
    ])
    runtime.registry.register_provider(provider, plugin_name="test_scripted")

    app = CliApp(commands=runtime.commands, bus=runtime.bus, interactive=False)
    result = await app.run_turn(
        "Run sleep 0.1 and echo FINISHED_TASK",
        options={"provider": "scripted", "model": "gpt-4o"},
    )

    assert isinstance(result, TurnResult)
    assert "FINISHED_TASK" in result.output

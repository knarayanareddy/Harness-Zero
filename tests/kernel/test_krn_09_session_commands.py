"""
Unit and integration tests for KRN-09:
Session, SessionManager, and Commands API.
Tests required (§5.1, §5.2):
1. session id validation (valid passes, path traversal or malformed raises ValueError)
2. concurrent submit -> TurnInProgress (409-equivalent)
3. restart -> sessions listed and history reloaded from JSONL logs
4. git exclude entry written to .git/info/exclude
5. SESSION_START hook invoked on session.open()
6. files.read and files.write through workspace jail
7. settings.get and settings.set via Commands
8. models.list aggregation across providers
9. generic command dispatch routing
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import Session, SessionManager
from harness.kernel.settings import SettingsStore
from harness.kernel.validators import validate_session_id
from harness.sdk.contracts import Hook
from harness.sdk.errors import PathOutsideWorkspace, TurnInProgress
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    HookEvent,
    HookOutcome,
    ModelInfo,
    TextDelta,
)


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "test_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def settings(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def secrets(tmp_path: Path) -> Secrets:
    return Secrets(tmp_path / "secrets.json")


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def registry(
    workspace_dir: Path, settings: SettingsStore, secrets: Secrets, event_bus: EventBus
) -> PluginRegistry:
    return PluginRegistry(
        workspace=workspace_dir,
        settings=settings,
        secrets=secrets,
        bus=event_bus,
    )


@pytest.fixture
def gate(
    workspace_dir: Path,
    registry: PluginRegistry,
    settings: SettingsStore,
    secrets: Secrets,
    event_bus: EventBus,
) -> PolicyGate:
    jail = WorkspaceJail(workspace_dir)
    approvals = ApprovalBroker(event_bus)
    return PolicyGate(
        registry=registry,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=event_bus,
    )


@pytest.fixture
def runner(
    registry: PluginRegistry,
    gate: PolicyGate,
    settings: SettingsStore,
    event_bus: EventBus,
) -> TurnRunner:
    return TurnRunner(
        registry=registry,
        gate=gate,
        settings=settings,
        bus=event_bus,
    )


@pytest.fixture
def session_manager(
    workspace_dir: Path,
    registry: PluginRegistry,
    settings: SettingsStore,
    event_bus: EventBus,
) -> SessionManager:
    return SessionManager(
        workspace=workspace_dir,
        registry=registry,
        settings=settings,
        bus=event_bus,
    )


@pytest.fixture
def commands(
    workspace_dir: Path,
    session_manager: SessionManager,
    runner: TurnRunner,
    gate: PolicyGate,
    settings: SettingsStore,
    secrets: Secrets,
    registry: PluginRegistry,
    event_bus: EventBus,
) -> Commands:
    return Commands(
        workspace=workspace_dir,
        session_manager=session_manager,
        runner=runner,
        gate=gate,
        settings=settings,
        secrets=secrets,
        registry=registry,
        bus=event_bus,
    )


def test_session_id_validation():
    """KRN-09 Test 1: session id validation."""
    # Valid IDs: must match session_[0-9a-f]{12}
    assert validate_session_id("session_0123456789ab") == "session_0123456789ab"
    assert validate_session_id("session_abcdef012345") == "session_abcdef012345"

    # Invalid IDs
    with pytest.raises(ValueError):
        validate_session_id("sess_12345")

    with pytest.raises(ValueError):
        validate_session_id("087307a1-6817-4073-91e9-e2619638ae31")

    with pytest.raises(ValueError):
        validate_session_id("abc-def_123")

    with pytest.raises(ValueError):
        validate_session_id("../escape")

    with pytest.raises(ValueError):
        validate_session_id("slash/path")

    with pytest.raises(ValueError):
        validate_session_id("")

    with pytest.raises(ValueError):
        validate_session_id("has spaces")

    with pytest.raises(ValueError):
        validate_session_id("has$special*char")


@pytest.mark.asyncio
async def test_concurrent_submit_raises_turn_in_progress(
    commands: Commands,
    registry: PluginRegistry,
):
    """
    KRN-09 Test 2: concurrent submit_turn to the same session raises TurnInProgress (409 equivalent).
    """
    # Slow provider that yields, waits on event, and completes
    release_first = asyncio.Event()

    class PausingProvider(ScriptedProvider):
        async def generate(self, req):
            yield TextDelta(text="First turn started...")
            await release_first.wait()
            yield TextDelta(text="First turn done.")
            yield Done(stop_reason="end_turn")

    registry.register_provider(PausingProvider())

    session_id = "session_000000000001"

    # Launch first turn
    task1 = asyncio.create_task(
        commands.submit_turn(session_id=session_id, prompt="Turn 1 prompt")
    )

    # Allow task1 to start and acquire the session lock
    await asyncio.sleep(0.05)

    # Attempt second turn on same session while first is still running
    with pytest.raises(TurnInProgress) as exc_info:
        await commands.submit_turn(session_id=session_id, prompt="Turn 2 prompt")

    assert exc_info.value.session_id == session_id
    assert exc_info.value.status_code == 409
    assert "Turn already in progress" in str(exc_info.value)

    # Finish task1
    release_first.set()
    res1 = await task1
    assert "First turn done." in res1.output


@pytest.mark.asyncio
async def test_restart_lists_sessions_and_reloads_history(
    workspace_dir: Path,
    registry: PluginRegistry,
    settings: SettingsStore,
    event_bus: EventBus,
):
    """
    KRN-09 Test 3: restart loads persisted sessions and reads history from JSONL logs.
    """
    sm1 = SessionManager(workspace_dir, registry, settings, event_bus)
    s1 = await sm1.create_session("session_000000000002", metadata={"title": "Project Refactor"})

    # Append transcript records
    await s1.transcript.append({
        "type": "USER",
        "source": "USER",
        "content": "Please inspect database schema",
    })
    await s1.transcript.append({
        "type": "ASSISTANT",
        "source": "MODEL",
        "content": "Schema inspected. 12 tables found.",
    })

    # Simulate restart by creating fresh SessionManager pointing to same workspace
    sm2 = SessionManager(workspace_dir, registry, settings, event_bus)
    sessions = sm2.list_sessions()

    assert any(s["session_id"] == "session_000000000002" for s in sessions)

    loaded_session = sm2.get_session("session_000000000002")
    assert loaded_session is not None
    assert loaded_session.metadata.get("title") == "Project Refactor"

    records = await loaded_session.transcript.read()
    assert len(records) == 2
    assert records[0]["content"] == "Please inspect database schema"
    assert records[1]["content"] == "Schema inspected. 12 tables found."


@pytest.mark.asyncio
async def test_git_exclude_written_when_git_dir_present(
    workspace_dir: Path,
    registry: PluginRegistry,
    settings: SettingsStore,
    event_bus: EventBus,
):
    """
    KRN-09 Test 4: writes /.harness/ to .git/info/exclude when .git exists (§5.1).
    """
    git_dir = workspace_dir / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)

    session = Session(
        session_id="session_000000000003",
        workspace=workspace_dir,
        registry=registry,
        settings=settings,
        bus=event_bus,
    )
    session.ensure_git_exclude()

    exclude_file = git_dir / "info" / "exclude"
    assert exclude_file.exists()
    content = exclude_file.read_text(encoding="utf-8")
    assert "/.harness/" in content

    # Second call must be idempotent
    session.ensure_git_exclude()
    lines = [line.strip() for line in exclude_file.read_text(encoding="utf-8").splitlines() if line.strip() == "/.harness/"]
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_session_start_hook_fired_on_open(
    workspace_dir: Path,
    registry: PluginRegistry,
    settings: SettingsStore,
    event_bus: EventBus,
):
    """
    KRN-09 Test 5: SESSION_START hooks are invoked when session.open() runs (§5.1).
    """
    hook_fired = False

    class OnSessionStartHook(Hook):
        name: str = "init_memory"
        events: frozenset[HookEvent] = frozenset([HookEvent.SESSION_START])
        matcher: str | None = None
        timeout_s: float = 2.0

        async def on(self, event, payload, ctx):
            nonlocal hook_fired
            if event == HookEvent.SESSION_START:
                hook_fired = True
            return HookOutcome(action="continue")

    registry.register_hook(OnSessionStartHook())

    sm = SessionManager(workspace_dir, registry, settings, event_bus)
    await sm.create_session("session_000000000004")

    assert hook_fired is True


@pytest.mark.asyncio
async def test_files_read_and_write_via_commands_in_jail(
    commands: Commands,
    workspace_dir: Path,
):
    """
    KRN-09 Test 6: files.read and files.write through workspace jail as user principal.
    """
    # 1. Write file
    write_res = await commands.files_write(
        path="src/config.json",
        content='{"debug": true}',
    )
    assert write_res["status"] == "ok"
    assert (workspace_dir / "src" / "config.json").exists()

    # 2. Read file
    read_res = await commands.files_read("src/config.json")
    assert read_res["content"] == '{"debug": true}'
    assert read_res["is_binary"] is False

    # 3. Path traversal escape attempts must be rejected by jail
    with pytest.raises(PathOutsideWorkspace):
        await commands.files_read("../../../etc/passwd")

    with pytest.raises(PathOutsideWorkspace):
        await commands.files_write("../evil.sh", "echo bad")


@pytest.mark.asyncio
async def test_settings_get_and_set_via_commands(commands: Commands):
    """
    KRN-09 Test 7: settings.get and settings.set through Commands API.
    """
    # Default
    val = await commands.settings_get("kernel.strategy")
    assert val == "single_loop"

    # Set new setting
    res = await commands.settings_set("kernel.strategy", "architect_editor")
    assert res["status"] == "ok"

    updated = await commands.settings_get("kernel.strategy")
    assert updated == "architect_editor"


@pytest.mark.asyncio
async def test_models_list_aggregates_providers(
    commands: Commands,
    registry: PluginRegistry,
):
    """
    KRN-09 Test 8: models.list aggregates models across registered providers.
    """
    provider_a = ScriptedProvider(
        models=[ModelInfo(id="model-a1", display_name="Model A1")]
    )
    provider_a.name = "mock_provider_a"

    provider_b = ScriptedProvider(
        models=[ModelInfo(id="model-b1", display_name="Model B1")]
    )
    provider_b.name = "mock_provider_b"

    registry.register_provider(provider_a)
    registry.register_provider(provider_b)

    models = await commands.models_list()
    model_ids = [m["id"] for m in models]
    assert "model-a1" in model_ids
    assert "model-b1" in model_ids


@pytest.mark.asyncio
async def test_models_list_rows_are_model_rows_with_provider_truth(
    commands: Commands,
    registry: PluginRegistry,
    caplog: pytest.LogCaptureFixture,
):
    """P4: `models.list` rows are `ModelRow` dicts; `status` comes from the provider's own
    `is_configured()` / `is_reachable()`, the command never raises and never logs above DEBUG."""
    from dataclasses import fields

    from harness.sdk.types import ModelRow

    ready = ScriptedProvider(models=[ModelInfo(id="ready-1", display_name="Ready")])
    ready.name = "ready_provider"

    needs_key = ScriptedProvider(models=[ModelInfo(id="keyed-1", display_name="Keyed")])
    needs_key.name = "keyed_provider"
    needs_key.configured = False

    down = ScriptedProvider(models=[ModelInfo(id="local-1", display_name="Local")])
    down.name = "local_provider"
    down.reachable = False

    class Broken:
        name = "broken_provider"
        caps = ready.caps

        def is_configured(self) -> bool:
            return True

        async def is_reachable(self) -> bool | None:
            raise RuntimeError("probe exploded")

        async def list_models(self):  # pragma: no cover - never reached
            return []

        async def generate(self, req):  # pragma: no cover - never reached
            yield Done(stop_reason="end_turn")

        async def aclose(self) -> None:
            pass

    for p in (ready, needs_key, down, Broken()):
        registry.register_provider(p)

    with caplog.at_level(logging.DEBUG, logger="harness.kernel.commands"):
        rows = await commands.models_list()

    by_id = {r["id"]: r for r in rows}
    expected_keys = {f.name for f in fields(ModelRow)} | {"status"}
    assert all(set(r) == expected_keys for r in rows), [set(r) ^ expected_keys for r in rows]

    assert by_id["ready-1"]["status"] == "ready" and by_id["ready-1"]["configured"] is True and by_id["ready-1"]["reachable"] is None
    assert by_id["keyed-1"]["status"] == "needs_key" and by_id["keyed-1"]["configured"] is False
    assert by_id["local-1"]["status"] == "unreachable" and by_id["local-1"]["reachable"] is False
    assert by_id["ready-1"]["provider"] == "ready_provider"

    # The broken provider contributes nothing, loudly only at DEBUG.
    assert "broken_provider" not in {r["provider"] for r in rows}
    noisy = [rec for rec in caplog.records if rec.levelno > logging.DEBUG and "broken_provider" in rec.getMessage()]
    assert noisy == [], [rec.getMessage() for rec in noisy]
    assert any("broken_provider" in rec.getMessage() for rec in caplog.records if rec.levelno == logging.DEBUG)


@pytest.mark.asyncio
async def test_generic_command_dispatch(commands: Commands):
    """
    KRN-09 Test 9: generic dispatch router handles RPC string names and custom commands.
    """
    # Built-in dispatch
    res = await commands.dispatch("settings.get", {"path": "kernel.strategy"})
    assert res == "single_loop"

    # Custom command registration
    def custom_ping(target: str = "all"):
        return f"pong-{target}"

    commands.register_command("custom.ping", custom_ping)
    res_custom = await commands.dispatch("custom.ping", {"target": "gateway"})
    assert res_custom == "pong-gateway"


def test_registry_rejects_providers_that_skip_the_contract(registry: PluginRegistry):
    """P4: a provider without `is_configured` / `is_reachable` is a plugin bug and fails at load, not later."""
    from harness.sdk.errors import PluginLoadError

    class HalfProvider:
        name = "half"
        caps = ScriptedProvider().caps

        async def list_models(self):
            return []

        async def generate(self, req):  # pragma: no cover
            yield None

        async def aclose(self):
            pass

    with pytest.raises(PluginLoadError, match="missing: is_configured, is_reachable"):
        registry.register_provider(HalfProvider(), plugin_name="half-plugin")
    assert registry.get_provider("half") is None

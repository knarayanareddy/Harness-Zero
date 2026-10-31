"""WS-06b: a setting is not confinement; the host probe, policy and executor result agree about the boundary.

The fake backend exercises all decisions on every CI host. Linux's real bwrap test is deliberately conditional on
the backend's namespace probe (not on `which bwrap`): a present binary with disabled user namespaces is unavailable,
not evidence that the filesystem has been confined.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.gate import PermissionModesPolicy as FallbackPermissionModesPolicy
from harness.kernel.gate import PolicyGate
from harness.kernel.inbox import BackgroundInbox
from harness.kernel.jail import WorkspaceJail
from harness.kernel.presets import PRESET_ORDER
from harness.kernel.registry import PluginRegistry
from harness.kernel.sandbox import SandboxedExecutor, SandboxedProcess, SandboxService, SandboxUnavailableError
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.executors.local.executor import LocalExecutor
from harness.plugins.executors.sandbox_bwrap.backend import BwrapBackend
from harness.plugins.policy.permission_modes.policy import PermissionModesPolicy
from harness.plugins.tools.fs.tools import (
    VIEW_FILE_SPEC,
    WRITE_FILE_SPEC,
    handle_view_file,
    handle_write_to_file,
)
from harness.sdk.sandbox_seatbelt import SeatbeltBackend
from harness.sdk.types import (
    Action,
    ExecRequest,
    ExecResult,
    RiskClass,
    SandboxAvailability,
    SandboxPolicy,
    ToolCall,
    ToolResult,
    ToolSpec,
)


class FakeProcess:
    id = "fake-process"
    pid = 101

    def __init__(self, exit_code: int = 0):
        self._expected_exit_code = exit_code
        self.returncode: int | None = None

    async def output(self):
        if False:
            yield "stdout", ""

    async def send_input(self, text: str) -> None:
        return None

    async def kill(self, grace_s: float = 2.0) -> None:
        return None

    async def wait(self) -> ExecResult:
        self.returncode = self._expected_exit_code
        return ExecResult(exit_code=self._expected_exit_code, stdout="ran", stderr="", truncated=False, duration_ms=1)


class RecordingExecutor:
    name = "recording"
    isolation = "none"

    def __init__(self):
        self.requests: list[ExecRequest] = []

    async def start(self, req: ExecRequest) -> FakeProcess:
        self.requests.append(req)
        return FakeProcess()


class FakeBackend:
    name = "bwrap"

    def __init__(self, *, available: bool):
        self._available = available
        self.requests: list[tuple[ExecRequest, SandboxPolicy]] = []

    def available(self) -> SandboxAvailability:
        return SandboxAvailability(
            backend=self.name,
            available=self._available,
            reason="fake probe passed" if self._available else "fake probe blocked",
            enforcement="full" if self._available else "none",
        )

    def wrap(self, req: ExecRequest, policy: SandboxPolicy) -> ExecRequest:
        self.requests.append((req, policy))
        return replace(req, command=["sandbox-wrapper", "--", str(req.command)], shell=False)


EXEC_SPEC = ToolSpec(
    name="run_command",
    description="test executor tool",
    input_schema={"type": "object"},
    risk_class=RiskClass.EXEC,
    capabilities=frozenset({"exec"}),
)
EXEC_CALL = ToolCall(id="c1", name="run_command", arguments={"command": "true"})
POLICY_CONTEXT = SimpleNamespace(workspace=Path("."), capabilities=frozenset())


class StaticSandbox:
    def __init__(self, status: dict[str, object]):
        self._status = status

    def status(self) -> dict[str, object]:
        return self._status


class RaisingSandbox:
    def __init__(self):
        self.status_calls = 0

    def status(self) -> dict[str, object]:
        self.status_calls += 1
        raise AssertionError("non-executor permission evaluation must not probe sandbox status")


POLICY_IMPLEMENTATIONS = [
    pytest.param(PermissionModesPolicy, id="plugin-policy"),
    pytest.param(FallbackPermissionModesPolicy, id="gate-fallback"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_factory", POLICY_IMPLEMENTATIONS)
@pytest.mark.parametrize(
    "spec",
    [pytest.param(VIEW_FILE_SPEC, id="filesystem-read"), pytest.param(WRITE_FILE_SPEC, id="filesystem-write")],
)
async def test_non_executor_filesystem_decisions_do_not_probe_sandbox_status(
    tmp_path: Path, policy_factory, spec: ToolSpec
):
    settings = settings_at(tmp_path, "open")
    settings.set("kernel.security.permission_mode", "ask")
    settings.set("kernel.sandbox.mode", "required")
    call = ToolCall(id="filesystem-call", name=spec.name, arguments={})
    normal_status = {
        "mode": "required",
        "backend": "fixture",
        "available": True,
        "enforcement": "full",
        "reason": "fixture probe passed",
        "network": False,
    }
    expected = await policy_factory(settings, StaticSandbox(normal_status)).evaluate(call, spec, POLICY_CONTEXT)
    sandbox = RaisingSandbox()

    actual = await policy_factory(settings, sandbox).evaluate(call, spec, POLICY_CONTEXT)

    assert sandbox.status_calls == 0
    assert actual == expected
    assert actual.metadata == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy_factory", "spec", "sandbox_mode", "preset", "permission_mode", "available", "expected_action", "expected_reason"),
    [
        pytest.param(
            PermissionModesPolicy, EXEC_SPEC, "required", "open", "bypass", False, Action.DENY,
            "sandbox unavailable on this host: fixture probe blocked", id="required-unavailable-exec-deny",
        ),
        pytest.param(
            FallbackPermissionModesPolicy, EXEC_SPEC, "required", "open", "bypass", False, Action.DENY,
            "sandbox unavailable on this host: fixture probe blocked", id="required-unavailable-exec-deny-fallback",
        ),
        pytest.param(
            PermissionModesPolicy, EXEC_SPEC, "preferred", "open", "allow", False, Action.ASK,
            "Sandbox unavailable; approve unconfined execution for 'run_command': fixture probe blocked",
            id="preferred-unavailable-exec-asks",
        ),
        pytest.param(
            FallbackPermissionModesPolicy, EXEC_SPEC, "preferred", "open", "allow", False, Action.ASK,
            "Sandbox unavailable; approve unconfined execution for 'run_command': fixture probe blocked",
            id="preferred-unavailable-exec-asks-fallback",
        ),
        pytest.param(
            PermissionModesPolicy, replace(WRITE_FILE_SPEC, capabilities=WRITE_FILE_SPEC.capabilities | {"exec"}),
            "required", "standard", "ask", True, Action.ASK,
            "Approval required for 'write_to_file' (write).", id="available-write-exec-cap-still-asks",
        ),
        pytest.param(
            FallbackPermissionModesPolicy, replace(WRITE_FILE_SPEC, capabilities=WRITE_FILE_SPEC.capabilities | {"exec"}),
            "required", "standard", "ask", True, Action.ASK,
            "Approval required for 'write_to_file' (write).", id="available-write-exec-cap-fallback-still-asks",
        ),
        pytest.param(
            PermissionModesPolicy, replace(VIEW_FILE_SPEC, capabilities=VIEW_FILE_SPEC.capabilities | {"exec"}),
            "required", "open", "allow", True, Action.ALLOW, "", id="read-exec-cap-queries-sandbox",
        ),
        pytest.param(
            FallbackPermissionModesPolicy, replace(VIEW_FILE_SPEC, capabilities=VIEW_FILE_SPEC.capabilities | {"exec"}),
            "required", "open", "allow", True, Action.ALLOW, "", id="read-exec-cap-queries-sandbox-fallback",
        ),
    ],
)
async def test_executor_backed_decisions_still_probe_sandbox_status_once(
    tmp_path: Path,
    policy_factory,
    spec: ToolSpec,
    sandbox_mode: str,
    preset: str,
    permission_mode: str,
    available: bool,
    expected_action: Action,
    expected_reason: str,
):
    settings = settings_at(tmp_path, preset)
    settings.set("kernel.security.permission_mode", permission_mode)
    settings.set("kernel.sandbox.mode", sandbox_mode)
    status = {
        "mode": sandbox_mode,
        "backend": "fixture",
        "available": available,
        "enforcement": "full" if available else "none",
        "reason": "fixture probe passed" if available else "fixture probe blocked",
        "network": False,
    }

    class CountingSandbox(StaticSandbox):
        def __init__(self, value: dict[str, object]):
            super().__init__(value)
            self.status_calls = 0

        def status(self) -> dict[str, object]:
            self.status_calls += 1
            return super().status()

    sandbox = CountingSandbox(status)
    call = ToolCall(id="executor-call", name=spec.name, arguments={})

    decision = await policy_factory(settings, sandbox).evaluate(call, spec, POLICY_CONTEXT)

    assert sandbox.status_calls == 1
    assert decision.action == expected_action
    assert decision.reason == expected_reason
    assert decision.metadata == {"sandbox": status}


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", PRESET_ORDER)
@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(EXEC_SPEC, id="exec-risk"),
        pytest.param(
            replace(WRITE_FILE_SPEC, capabilities=WRITE_FILE_SPEC.capabilities | {"exec"}),
            id="write-with-exec-capability",
        ),
        pytest.param(
            replace(VIEW_FILE_SPEC, capabilities=VIEW_FILE_SPEC.capabilities | {"exec"}),
            id="read-with-exec-capability",
        ),
    ],
)
async def test_fallback_permission_decisions_match_plugin_for_supported_presets(
    tmp_path: Path, preset: str, spec: ToolSpec
):
    settings = settings_at(tmp_path, preset)
    sandbox_mode = settings.get("kernel.sandbox.mode", "off")
    sandbox_available = sandbox_mode != "off"
    status = {
        "mode": sandbox_mode,
        "backend": "bwrap" if sandbox_available else "none",
        "available": sandbox_available,
        "enforcement": "full" if sandbox_available else "none",
        "reason": "fixture probe passed" if sandbox_available else "Sandbox mode is off.",
        "network": settings.get("kernel.sandbox.network", False),
    }
    call = ToolCall(id="preset-parity", name=spec.name, arguments={})

    plugin_decision = await PermissionModesPolicy(settings, StaticSandbox(status)).evaluate(
        call, spec, POLICY_CONTEXT
    )
    fallback_decision = await FallbackPermissionModesPolicy(settings, StaticSandbox(status)).evaluate(
        call, spec, POLICY_CONTEXT
    )

    if preset == "locked" and spec.risk_class in (RiskClass.WRITE, RiskClass.EXEC):
        expected_action = Action.DENY
        expected_reason = f"Execution of '{call.name}' denied in plan mode."
    elif preset == "standard" and spec.risk_class == RiskClass.EXEC:
        expected_action = Action.ALLOW
        expected_reason = ""
    elif preset in ("careful", "standard") and spec.risk_class in (RiskClass.WRITE, RiskClass.EXEC):
        expected_action = Action.ASK
        expected_reason = f"Approval required for '{call.name}' ({spec.risk_class.value})."
    else:
        expected_action = Action.ALLOW
        expected_reason = ""

    assert fallback_decision == plugin_decision
    assert fallback_decision.action == expected_action
    assert fallback_decision.reason == expected_reason
    assert fallback_decision.metadata == {"sandbox": status}


@pytest.mark.asyncio
async def test_explicit_allow_override_still_allows_write_with_exec_capability(tmp_path: Path):
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.security.permission_mode", "allow")
    status = {
        "mode": "preferred",
        "backend": "bwrap",
        "available": True,
        "enforcement": "full",
        "reason": "fixture probe passed",
        "network": False,
    }
    spec = replace(WRITE_FILE_SPEC, capabilities=WRITE_FILE_SPEC.capabilities | {"exec"})
    call = ToolCall(id="explicit-allow", name=spec.name, arguments={})

    plugin_decision = await PermissionModesPolicy(settings, StaticSandbox(status)).evaluate(
        call, spec, POLICY_CONTEXT
    )
    fallback_decision = await FallbackPermissionModesPolicy(settings, StaticSandbox(status)).evaluate(
        call, spec, POLICY_CONTEXT
    )

    assert plugin_decision == fallback_decision
    assert fallback_decision.action == Action.ALLOW
    assert fallback_decision.reason == ""
    assert fallback_decision.metadata == {"sandbox": status}


@pytest.mark.asyncio
async def test_fallback_gate_requires_approval_for_standard_write_with_exec_capability(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bus = EventBus()
    settings = settings_at(tmp_path, "standard")
    settings.set("kernel.sandbox.backend", "bwrap")
    secrets = Secrets(tmp_path / "secrets.json")
    registry = PluginRegistry(workspace=workspace, settings=settings, secrets=secrets, bus=bus)
    registry.sandbox.register_backend(FakeBackend(available=True), "test-sandbox")
    approvals = ApprovalBroker(bus=bus)
    gate = PolicyGate(
        registry=registry,
        jail=WorkspaceJail(workspace),
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=bus,
    )
    assert isinstance(gate.permission_modes_policy, FallbackPermissionModesPolicy)
    spec = replace(WRITE_FILE_SPEC, capabilities=WRITE_FILE_SPEC.capabilities | {"exec"})
    handler_started = False

    async def handler(_args, _ctx):
        nonlocal handler_started
        handler_started = True
        return ToolResult.success("handler ran")

    registry._register_tool(spec, handler, "fallback-parity-test")

    result = await gate.execute(
        ToolCall(
            id="fallback-write",
            name=spec.name,
            arguments={"file_path": "written.txt", "content": "must not run"},
        )
    )

    assert result.is_error is True
    assert "Approval denied: non-interactive session" in result.content[0].text
    assert handler_started is False


@pytest.mark.asyncio
async def test_gate_keeps_executor_wrapper_without_status_probe_for_nonexec_filesystem_handlers(
    tmp_path: Path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "existing.txt"
    source.write_text("read through the real filesystem handler\n", encoding="utf-8")
    bus = EventBus()
    settings = settings_at(tmp_path, "standard")
    settings.set("kernel.sandbox.backend", "none")
    secrets = Secrets(tmp_path / "secrets.json")
    registry = PluginRegistry(workspace=workspace, settings=settings, secrets=secrets, bus=bus)
    executor = RecordingExecutor()
    registry.register_executor(executor)
    approvals = ApprovalBroker(bus=bus)
    gate = PolicyGate(
        registry=registry,
        jail=WorkspaceJail(workspace),
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=bus,
    )
    status_calls = 0

    def unexpected_status():
        nonlocal status_calls
        status_calls += 1
        raise AssertionError("non-executor filesystem handlers must not probe sandbox status during admission")

    monkeypatch.setattr(registry.sandbox, "status", unexpected_status)
    wrapped_executors: list[SandboxedExecutor] = []
    wrap_executor = registry.sandbox.wrap_executor

    def record_wrapper(delegate, *, allow_unconfined=False):
        wrapped = wrap_executor(delegate, allow_unconfined=allow_unconfined)
        assert isinstance(wrapped, SandboxedExecutor)
        wrapped_executors.append(wrapped)
        return wrapped

    monkeypatch.setattr(registry.sandbox, "wrap_executor", record_wrapper)
    async def read_handler(args, ctx):
        return await handle_view_file(args, ctx)

    async def write_handler(args, ctx):
        return await handle_write_to_file(args, ctx)

    registry.register_tool(VIEW_FILE_SPEC, read_handler, "filesystem-fixture")
    registry.register_tool(WRITE_FILE_SPEC, write_handler, "filesystem-fixture")
    read_result = await gate.execute(
        ToolCall(id="nonexec-read", name=VIEW_FILE_SPEC.name, arguments={"file_path": "existing.txt"})
    )

    assert read_result.is_error is False
    assert "read through the real filesystem handler" in read_result.content[0].text
    assert read_result.metadata["read_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert status_calls == 0
    assert len(wrapped_executors) == 1

    subscriber = bus.subscribe(interactive=True)
    write_task = asyncio.create_task(
        gate.execute(
            ToolCall(
                id="approved-nonexec-write",
                name=WRITE_FILE_SPEC.name,
                arguments={"file_path": "created.txt", "content": "approved actual write\n"},
            )
        )
    )
    try:
        approval_event = None
        while approval_event is None:
            event = await asyncio.wait_for(subscriber.get(), timeout=5.0)
            if event.type == "approval.required":
                approval_event = event
        approved = await approvals.resolve_recorded(
            approval_event.data["approval_id"],
            approved=True,
            by="test-user",
            reason="explicit fixture approval",
        )
        assert approved is True
        write_result = await asyncio.wait_for(write_task, timeout=5.0)
    finally:
        if not write_task.done():
            write_task.cancel()
            await asyncio.gather(write_task, return_exceptions=True)
        bus.unsubscribe(subscriber)

    assert write_result.is_error is False
    assert (workspace / "created.txt").read_text(encoding="utf-8") == "approved actual write\n"
    assert status_calls == 0
    assert len(wrapped_executors) == 2


@pytest.mark.parametrize("available", [True, False], ids=["available", "unavailable"])
def test_sandboxed_executor_isolation_is_lazy_and_uses_real_status(tmp_path: Path, monkeypatch, available: bool):
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "bwrap" if available else "none")
    backend = FakeBackend(available=available) if available else None
    service = service_for(settings, tmp_path, backend)
    original_status = service.status
    status_calls = 0

    def count_status():
        nonlocal status_calls
        status_calls += 1
        return original_status()

    monkeypatch.setattr(service, "status", count_status)
    delegate = RecordingExecutor()
    wrapped = SandboxedExecutor(delegate, service)

    assert status_calls == 0
    expected_isolation = "os-sandbox" if available else delegate.isolation
    assert wrapped.isolation == expected_isolation
    assert status_calls == 1
    assert wrapped.isolation == expected_isolation
    assert status_calls == 1


@pytest.mark.asyncio
async def test_sandboxed_executor_start_still_blocks_required_unavailable_before_delegate(tmp_path: Path):
    settings = settings_at(tmp_path, "locked")
    settings.set("kernel.sandbox.backend", "none")
    service = service_for(settings, tmp_path, None)
    delegate = RecordingExecutor()
    wrapped = SandboxedExecutor(delegate, service)

    with pytest.raises(SandboxUnavailableError, match="Required OS sandbox unavailable"):
        await wrapped.start(ExecRequest(command="true", cwd=tmp_path))

    assert delegate.requests == []


def settings_at(tmp_path: Path, preset: str = "careful") -> SettingsStore:
    settings = SettingsStore(tmp_path / "settings.json")
    settings.set("kernel.security.preset", preset)
    return settings


def service_for(settings: SettingsStore, workspace: Path, backend: FakeBackend | None) -> SandboxService:
    service = SandboxService(settings, workspace)
    if backend is not None:
        service.register_backend(backend, "test-sandbox")
    return service


def test_invalid_extra_readable_symlink_is_reported_unavailable(tmp_path: Path):
    a = tmp_path / "loop-a"
    b = tmp_path / "loop-b"
    a.symlink_to(b)
    b.symlink_to(a)
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.extra_readable", [str(a)])
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, FakeBackend(available=True))

    status = service.status()
    assert status["available"] is False
    assert status["enforcement"] == "none"
    assert "cannot be resolved" in status["reason"]


def test_invalid_backend_availability_is_normalized_to_fail_closed(tmp_path: Path):
    class InvalidAvailabilityBackend(FakeBackend):
        def available(self):
            return None

    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, InvalidAvailabilityBackend(available=True))

    status = service.status()
    assert status["available"] is False
    assert status["enforcement"] == "none"
    assert "invalid availability result" in status["reason"]


def test_malformed_availability_fields_are_normalized_to_fail_closed(tmp_path: Path):
    class MalformedAvailabilityBackend(FakeBackend):
        def available(self):
            return SandboxAvailability(
                backend=self.name,
                available="yes",  # type: ignore[arg-type]
                reason="the backend supplied a non-boolean availability fact",
                enforcement="full",
            )

    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, MalformedAvailabilityBackend(available=True))

    status = service.status()
    assert status["available"] is False
    assert status["enforcement"] == "none"
    assert "malformed availability facts" in status["reason"]


def test_partial_backend_reason_is_preserved_without_extra_readable_paths(tmp_path: Path):
    class PartialBackend(FakeBackend):
        name = "seatbelt"

        def available(self):
            return SandboxAvailability(
                backend=self.name,
                available=True,
                reason="Seatbelt profile accepted by this host",
                enforcement="partial",
            )

    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "seatbelt")
    status = service_for(settings, tmp_path, PartialBackend(available=True)).status()

    assert status["available"] is True
    assert status["enforcement"] == "partial"
    assert status["reason"] == "Seatbelt profile accepted by this host"


def test_extra_readable_path_downgrades_full_enforcement_truthfully(tmp_path: Path):
    readable = tmp_path / "sdk"
    readable.mkdir()
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "bwrap")
    settings.set("kernel.sandbox.extra_readable", [str(readable)])
    status = service_for(settings, tmp_path, FakeBackend(available=True)).status()

    assert status["available"] is True
    assert status["enforcement"] == "partial"
    assert status["reason"] == "Extra readable paths are exposed; review kernel.sandbox.extra_readable."


@pytest.mark.asyncio
async def test_required_unavailable_denies_even_bypass_before_executor_start(tmp_path: Path):
    settings = settings_at(tmp_path, "open")
    settings.set("kernel.security.permission_mode", "bypass")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "none")
    service = service_for(settings, tmp_path, None)
    decision = await PermissionModesPolicy(settings, service).evaluate(
        EXEC_CALL, EXEC_SPEC, POLICY_CONTEXT
    )

    assert decision.action == Action.DENY
    assert "sandbox unavailable on this host" in decision.reason
    assert decision.metadata["sandbox"]["available"] is False

    delegate = RecordingExecutor()
    wrapped = service.wrap_executor(delegate, allow_unconfined=True)
    with pytest.raises(SandboxUnavailableError, match="Required OS sandbox unavailable"):
        await wrapped.start(ExecRequest(command="true", cwd=tmp_path))
    assert delegate.requests == [], "required mode must not start the host executor even under bypass"


@pytest.mark.asyncio
async def test_preferred_unavailable_asks_before_unconfined_execution(tmp_path: Path):
    settings = settings_at(tmp_path, "open")
    settings.set("kernel.security.permission_mode", "allow")
    settings.set("kernel.sandbox.mode", "preferred")
    settings.set("kernel.sandbox.backend", "none")
    service = service_for(settings, tmp_path, None)

    decision = await PermissionModesPolicy(settings, service).evaluate(
        EXEC_CALL, EXEC_SPEC, POLICY_CONTEXT
    )
    assert decision.action == Action.ASK
    assert "approve unconfined execution" in decision.reason

    delegate = RecordingExecutor()
    with pytest.raises(SandboxUnavailableError, match="requires explicit approval"):
        await service.wrap_executor(delegate).start(ExecRequest(command="true", cwd=tmp_path))
    assert delegate.requests == []

    # A real approval from the gate permits the fallback, but the result names it `none`, not `full`.
    process = await service.wrap_executor(delegate, allow_unconfined=True).start(
        ExecRequest(command="true", cwd=tmp_path)
    )
    result = await process.wait()
    assert result.metadata["sandbox"]["available"] is False
    assert result.metadata["sandbox"]["enforcement"] == "none"
    assert len(delegate.requests) == 1


@pytest.mark.asyncio
async def test_standard_allows_exec_only_after_backend_probe_succeeds(tmp_path: Path):
    settings = settings_at(tmp_path, "standard")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, FakeBackend(available=True))
    decision = await PermissionModesPolicy(settings, service).evaluate(
        EXEC_CALL, EXEC_SPEC, POLICY_CONTEXT
    )

    assert decision.action == Action.ALLOW
    assert decision.metadata["sandbox"]["available"] is True
    assert decision.metadata["sandbox"]["enforcement"] == "full"


@pytest.mark.asyncio
async def test_standard_does_not_auto_allow_write_tools_that_use_an_executor(tmp_path: Path):
    settings = settings_at(tmp_path, "standard")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, FakeBackend(available=True))
    write_spec = ToolSpec(
        name="write_via_executor",
        description="a write-risk tool that delegates to a process",
        input_schema={"type": "object"},
        risk_class=RiskClass.WRITE,
        capabilities=frozenset({"exec"}),
    )

    decision = await PermissionModesPolicy(settings, service).evaluate(
        ToolCall(id="c-write", name=write_spec.name, arguments={}), write_spec, POLICY_CONTEXT
    )

    assert decision.action == Action.ASK, "standard only auto-allows EXEC, never a WRITE action"


@pytest.mark.asyncio
async def test_careful_still_asks_even_when_backend_is_available(tmp_path: Path):
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = service_for(settings, tmp_path, FakeBackend(available=True))
    decision = await PermissionModesPolicy(settings, service).evaluate(
        EXEC_CALL, EXEC_SPEC, POLICY_CONTEXT
    )
    assert decision.action == Action.ASK
    assert decision.metadata["sandbox"]["available"] is True


@pytest.mark.asyncio
async def test_off_mode_runs_but_result_still_reports_no_confinement(tmp_path: Path):
    settings = settings_at(tmp_path, "open")
    service = service_for(settings, tmp_path, None)
    delegate = RecordingExecutor()
    process = await service.wrap_executor(delegate).start(ExecRequest(command="true", cwd=tmp_path))
    result = await process.wait()

    assert result.metadata["sandbox"]["mode"] == "off"
    assert result.metadata["sandbox"]["enforcement"] == "none"
    assert result.metadata["sandbox"]["network"] is True
    assert len(delegate.requests) == 1


@pytest.mark.asyncio
async def test_background_task_reports_exec_result_exit_code_and_sandbox_metadata():
    sandbox_fact = {
        "mode": "required",
        "backend": "bwrap",
        "available": True,
        "enforcement": "full",
        "reason": "",
        "network": False,
    }
    process = SandboxedProcess(FakeProcess(exit_code=7), sandbox_fact)
    inbox = BackgroundInbox()
    inbox.register_task("task_nonzero", process, label="failing command")

    assert await inbox.wait_task("task_nonzero") == 7
    status = inbox.get_status("task_nonzero")
    assert status is not None
    assert status["exit_code"] == 7
    assert status["sandbox"] == sandbox_fact
    assert process.exit_code == process.returncode == 7


def test_seatbelt_profile_is_workspace_scoped_and_network_off_by_default(tmp_path: Path, monkeypatch):
    import subprocess

    monkeypatch.setattr("harness.sdk.sandbox_seatbelt.platform.system", lambda: "Darwin")
    monkeypatch.setattr("harness.sdk.sandbox_seatbelt.shutil.which", lambda _name: "/usr/bin/sandbox-exec")
    monkeypatch.setattr(
        "harness.sdk.sandbox_seatbelt.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = SeatbeltBackend()
    availability = backend.available()
    assert availability.available is True
    assert availability.enforcement == "partial"

    wrapped = backend.wrap(
        ExecRequest(command="touch inside.txt", cwd=workspace, shell=True),
        SandboxPolicy(workspace=workspace, writable=(workspace,), network=False),
    )

    assert isinstance(wrapped.command, list)
    assert wrapped.command[0] == "/usr/bin/sandbox-exec"
    profile = wrapped.command[2]
    assert '(allow file-read-data (literal "/"))' in profile
    assert '(subpath "/")' not in profile
    assert "(deny network*)" in profile
    assert f'(subpath "{workspace}")' in profile
    assert f'(allow file-write* (subpath "{workspace}") (subpath "/private/tmp"))' in profile
    assert wrapped.command[-3:] == ["/bin/sh", "-c", "touch inside.txt"]
    assert "HOME=/private/tmp" in wrapped.command
    assert wrapped.shell is False


def test_seatbelt_availability_probe_allows_root_directory_only(tmp_path: Path, monkeypatch):
    import subprocess

    profiles = []
    monkeypatch.setattr("harness.sdk.sandbox_seatbelt.platform.system", lambda: "Darwin")
    monkeypatch.setattr("harness.sdk.sandbox_seatbelt.shutil.which", lambda _name: "/usr/bin/sandbox-exec")

    def capture(command, **kwargs):
        profiles.append(command[2])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("harness.sdk.sandbox_seatbelt.subprocess.run", capture)
    assert SeatbeltBackend().available().available
    assert len(profiles) == 1
    assert '(allow file-read-data (literal "/"))' in profiles[0]
    assert '(subpath "/")' not in profiles[0]


@pytest.mark.parametrize("shell", [False, True])
def test_seatbelt_preserves_validated_nested_cwd(tmp_path: Path, monkeypatch, shell):
    workspace = tmp_path / "workspace"
    nested = workspace / "nested space"
    nested.mkdir(parents=True)
    backend = SeatbeltBackend()
    backend._executable = "/usr/bin/sandbox-exec"
    monkeypatch.setattr(backend, "available", lambda: SandboxAvailability(
        backend="seatbelt", available=True, reason="wrapper-only fixture", enforcement="partial"))
    command = "pwd" if shell else ["/bin/pwd"]
    request = ExecRequest(command=command, cwd=nested, shell=shell, timeout_s=7, interactive=True)
    wrapped = backend.wrap(request, SandboxPolicy(workspace=workspace, writable=(workspace,)))
    assert wrapped.cwd == nested.resolve()
    assert wrapped.timeout_s == 7 and wrapped.interactive is True and wrapped.shell is False
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "outside-link").symlink_to(outside, target_is_directory=True)
    for invalid in (outside, workspace / "outside-link"):
        with pytest.raises(ValueError, match="cwd must be a directory inside"):
            backend.wrap(ExecRequest(command=command, cwd=invalid, shell=shell), SandboxPolicy(workspace=workspace))


@pytest.mark.asyncio
async def test_seatbelt_native_nested_cwd_and_relative_write(tmp_path: Path):
    if sys.platform != "darwin":
        pytest.skip("Seatbelt native integration is macOS-only")
    workspace = tmp_path / "workspace"
    nested = workspace / "nested space"
    nested.mkdir(parents=True)
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "seatbelt")
    service = SandboxService(settings, workspace)
    service.register_backend(SeatbeltBackend(), "test-sandbox")
    availability = service.availability()
    if not availability.available:
        pytest.skip(f"Seatbelt native qualification unavailable: {availability.reason}")
    executor = service.wrap_executor(LocalExecutor(default_cwd=workspace))
    proc = await executor.start(ExecRequest(command=["/bin/sh", "-c", "pwd; printf nested > inside.txt"], cwd=nested))
    result = await proc.wait()
    assert result.exit_code == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == nested.resolve()
    assert (nested / "inside.txt").read_text() == "nested"
    assert not (workspace / "inside.txt").exists()
    assert result.metadata["sandbox"]["enforcement"] == "partial"


@pytest.mark.asyncio
async def test_seatbelt_native_root_directory_permission_does_not_expose_outside_files(tmp_path: Path):
    if sys.platform != "darwin":
        pytest.skip("Seatbelt native integration is macOS-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("synthetic outside content")
    marker = tmp_path / "outside-write.txt"
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "seatbelt")
    service = SandboxService(settings, workspace)
    service.register_backend(SeatbeltBackend(), "test-sandbox")
    availability = service.availability()
    if not availability.available:
        pytest.skip(f"Seatbelt native qualification unavailable: {availability.reason}")
    executor = service.wrap_executor(LocalExecutor(default_cwd=workspace))
    proc = await executor.start(ExecRequest(
        command=["/bin/sh", "-c", 'cat "$1"; printf changed > "$2"', "test", str(outside), str(marker)],
        cwd=workspace,
    ))
    result = await proc.wait()
    assert result.exit_code != 0
    assert "synthetic outside content" not in result.stdout
    assert outside.read_text() == "synthetic outside content"
    assert not marker.exists()
    assert result.metadata["sandbox"]["enforcement"] == "partial"


def test_bwrap_builder_is_argv_based_workspace_only_and_network_off_by_default(tmp_path: Path, monkeypatch):
    import subprocess

    monkeypatch.setattr("harness.sdk.sandbox_bwrap.platform.system", lambda: "Linux")
    monkeypatch.setattr("harness.sdk.sandbox_bwrap.shutil.which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        "harness.sdk.sandbox_bwrap.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = BwrapBackend()
    assert backend.available().available

    req = ExecRequest(command="touch inside.txt", cwd=workspace, shell=True)
    wrapped = backend.wrap(req, SandboxPolicy(workspace=workspace, writable=(workspace,), network=False))

    argv = wrapped.command
    assert isinstance(argv, list) and argv[0] == "/usr/bin/bwrap"
    assert ["--bind", str(workspace), str(workspace)] == argv[argv.index("--bind"):argv.index("--bind") + 3]
    assert "--tmpfs" in argv and argv[argv.index("--tmpfs") + 1] == "/tmp"
    assert "--unshare-net" in argv
    assert "--clearenv" in argv and "--setenv" in argv
    assert argv[-4:] == ["--", "/bin/sh", "-c", "touch inside.txt"]
    assert "/home" not in argv, "host home directories are absent unless the user granted an extra readable path"
    assert wrapped.cwd == workspace and wrapped.shell is False


@pytest.mark.asyncio
async def test_bwrap_real_backend_blocks_host_file_and_records_full_enforcement(tmp_path: Path):
    if not sys.platform.startswith("linux"):
        pytest.skip("bubblewrap integration is Linux-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("not mounted into the sandbox", encoding="utf-8")
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = SandboxService(settings, workspace)
    backend = BwrapBackend()
    service.register_backend(backend, "test-sandbox")
    availability = service.availability()
    if not availability.available:
        pytest.skip(f"bubblewrap integration unavailable: {availability.reason}")

    executor = service.wrap_executor(LocalExecutor(default_cwd=workspace))
    req = ExecRequest(command=["/bin/cat", str(outside)], cwd=workspace, shell=False)
    proc = await executor.start(req)
    result = await proc.wait()

    assert result.exit_code != 0
    assert "not mounted into the sandbox" not in result.stdout
    assert result.metadata["sandbox"]["available"] is True
    assert result.metadata["sandbox"]["enforcement"] == "full"


@pytest.mark.asyncio
async def test_bwrap_real_backend_writes_workspace_but_not_host_tmp(tmp_path: Path):
    if not sys.platform.startswith("linux"):
        pytest.skip("bubblewrap integration is Linux-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = SandboxService(settings, workspace)
    backend = BwrapBackend()
    service.register_backend(backend, "test-sandbox")
    availability = service.availability()
    if not availability.available:
        pytest.skip(f"bubblewrap integration unavailable: {availability.reason}")

    host_tmp_marker = tmp_path / "host-tmp-marker"
    executor = service.wrap_executor(LocalExecutor(default_cwd=workspace))
    req = ExecRequest(
        command=["/bin/sh", "-c", f"touch inside.txt; touch {host_tmp_marker}"],
        cwd=workspace,
        shell=False,
    )
    proc = await executor.start(req)
    result = await proc.wait()

    assert result.exit_code == 0, result.stderr
    assert (workspace / "inside.txt").exists()
    assert not host_tmp_marker.exists(), "sandbox /tmp is private; the host path is not a writable mount"


@pytest.mark.asyncio
async def test_bwrap_real_backend_unshares_loopback_network(tmp_path: Path):
    if not sys.platform.startswith("linux"):
        pytest.skip("bubblewrap integration is Linux-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = settings_at(tmp_path, "careful")
    settings.set("kernel.sandbox.mode", "required")
    settings.set("kernel.sandbox.backend", "bwrap")
    service = SandboxService(settings, workspace)
    backend = BwrapBackend()
    service.register_backend(backend, "test-sandbox")
    availability = service.availability()
    if not availability.available:
        pytest.skip(f"bubblewrap integration unavailable: {availability.reason}")

    connected = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connected.set()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    script = (
        "import socket,sys; s=socket.socket(); s.settimeout(1); "
        f"\ntry: s.connect(('127.0.0.1',{port})); sys.exit(9) "
        "\nexcept OSError: sys.exit(0)"
    )
    try:
        executor = service.wrap_executor(LocalExecutor(default_cwd=workspace))
        proc = await executor.start(
            ExecRequest(command=[sys.executable, "-c", script], cwd=workspace, shell=False)
        )
        result = await proc.wait()
        assert result.exit_code == 0, result.stderr
        assert not connected.is_set(), "the sandbox's private network namespace must not reach host loopback"
    finally:
        server.close()
        await server.wait_closed()

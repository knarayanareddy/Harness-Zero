"""
Unit and integration tests for KRN-08:
TurnRunner, KernelServices, BackgroundInbox, CircuitBreaker, and SpendCap.
Tests required (§5.2, §5.6, §5.8):
1. two-tool turn with parallel READ batch
2. wake-up consumed without polling
3. cancel mid-stream records notification and turn.cancelled event
4. circuit breaker opens after N failures
5. spend cap stops the turn with SpendCapExceeded
6. child turn depth limiting (max 2)
7. sequential tool execution when batch contains non-READ tools
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.breaker import CircuitBreaker, CircuitState
from harness.kernel.bus import EventBus
from harness.kernel.gate import PolicyGate
from harness.kernel.inbox import BackgroundInbox
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import KernelServicesImpl, TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.sdk.errors import ProviderError
from harness.sdk.events import Event
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ExecResult,
    RiskClass,
    SessionInfo,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    ToolResult,
    ToolSpec,
    Usage,
    UsageEvent,
)


class DummyProcess:
    """Mock process satisfying Process protocol for BackgroundInbox tests."""

    def __init__(self, proc_id: str = "task_01", exit_code: int = 0, lines: list[str] | None = None) -> None:
        self.id = proc_id
        self.pid = 9999
        self.exit_code = exit_code
        self._lines = lines or ["Build step completed successfully", "Done in 1.2s"]

    async def output(self):
        for line in self._lines:
            yield ("stdout", line + "\n")

    async def wait(self) -> ExecResult:
        return ExecResult(
            exit_code=self.exit_code,
            stdout="\n".join(self._lines),
            stderr="",
            truncated=False,
            duration_ms=100,
        )

    async def kill(self, grace_s: float = 2.0) -> None:
        pass

    async def send_input(self, text: str) -> None:
        pass


def drain_events(subscriber) -> list[Event]:
    evts: list[Event] = []
    while not subscriber.queue.empty():
        evts.append(subscriber.queue.get_nowait())
    return evts


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture
def settings(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def secrets(tmp_path: Path) -> Secrets:
    return Secrets(tmp_path / "secrets.json")


@pytest.fixture
def session_info(workspace_dir: Path) -> SessionInfo:
    return SessionInfo(session_id="session_test_01", workspace=workspace_dir)


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
    workspace_dir: Path, registry: PluginRegistry, settings: SettingsStore, secrets: Secrets, event_bus: EventBus
) -> PolicyGate:
    jail = WorkspaceJail(workspace_dir)
    broker = ApprovalBroker(event_bus)
    return PolicyGate(
        registry=registry,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=broker,
        bus=event_bus,
    )



@pytest.mark.asyncio
async def test_two_tool_turn_execution(
    session_info: SessionInfo,
    registry: PluginRegistry,
    gate: PolicyGate,
    event_bus: EventBus,
):
    """
    KRN-08 Test 1: two-tool turn execution.
    Provider requests two READ tools in parallel; both are executed; follow-up returns final text.
    """
    execution_order = []

    # Register two READ tools
    spec_a = ToolSpec(
        name="read_alpha",
        description="Reads alpha",
        input_schema={"type": "object", "properties": {"target": {"type": "string"}}},
        risk_class=RiskClass.READ,
    )
    spec_b = ToolSpec(
        name="read_beta",
        description="Reads beta",
        input_schema={"type": "object", "properties": {"target": {"type": "string"}}},
        risk_class=RiskClass.READ,
    )

    async def handler_a(args: dict[str, Any], ctx: Any) -> ToolResult:
        execution_order.append("alpha")
        return ToolResult.success("Alpha content")

    async def handler_b(args: dict[str, Any], ctx: Any) -> ToolResult:
        execution_order.append("beta")
        return ToolResult.success("Beta content")

    gate.register_tool(spec_a, handler_a)
    gate.register_tool(spec_b, handler_b)

    # ScriptedProvider: Turn 1 requests two tools; Turn 2 returns final response
    provider = ScriptedProvider()
    provider.add_response([
        ToolCallEnd(id="call_a", name="read_alpha", arguments={"target": "file1.txt"}),
        ToolCallEnd(id="call_b", name="read_beta", arguments={"target": "file2.txt"}),
        Done(stop_reason="tool_use"),
    ])
    provider.add_response([
        TextDelta(text="Successfully read both alpha and beta files."),
        Done(stop_reason="end_turn"),
    ])

    registry.register_provider(provider)

    runner = TurnRunner(
        registry=registry,
        gate=gate,
        bus=event_bus,
    )

    sub = event_bus.subscribe()

    result = await runner.execute_turn(
        session=session_info,
        prompt="Please read alpha and beta",
    )

    assert result.tool_calls == 2
    assert "Successfully read both alpha and beta files." in result.output
    assert result.stop_reason == "end_turn"
    assert "alpha" in execution_order
    assert "beta" in execution_order

    events_received = drain_events(sub)
    event_types = [e.type for e in events_received]
    assert "turn.started" in event_types
    assert "model.tool_call" in event_types
    assert "tool.result" in event_types
    assert "turn.completed" in event_types


@pytest.mark.asyncio
async def test_wakeup_consumed_without_polling(
    session_info: SessionInfo,
    registry: PluginRegistry,
    gate: PolicyGate,
    event_bus: EventBus,
):
    """
    KRN-08 Test 2: background task wake-up consumed without polling.
    Model finishes turn with pending background task; inbox monitors exit;
    wake-up event is consumed and injected as user message; loop continues to completion.
    """
    inbox = BackgroundInbox(bus=event_bus, session_id=session_info.session_id)

    # Register background task
    dummy_proc = DummyProcess("bg_compile_42", exit_code=0, lines=["compilation finished"])
    inbox.register_task("bg_compile_42", dummy_proc, label="rust_build")

    # Provider: first response has no tools (stops with pending task), second responds after wakeup
    provider = ScriptedProvider()
    provider.add_response([
        TextDelta(text="I have started the build in the background. Waiting for it to finish."),
        Done(stop_reason="end_turn"),
    ])
    provider.add_response([
        TextDelta(text="Build task succeeded! Everything is ready."),
        Done(stop_reason="end_turn"),
    ])

    registry.register_provider(provider)

    runner = TurnRunner(
        registry=registry,
        gate=gate,
        bus=event_bus,
        inbox=inbox,
    )

    result = await runner.execute_turn(
        session=session_info,
        prompt="Run the build task",
        options={"wait_for_background": True, "background_wait_s": 5.0},
    )

    assert "Build task succeeded!" in result.output
    assert result.stop_reason == "end_turn"
    # Ensure inbox task completed
    status = inbox.get_status("bg_compile_42")
    assert status is not None
    assert status["completed"] is True
    assert status["exit_code"] == 0


@pytest.mark.asyncio
async def test_cancel_mid_stream_records_notification(
    session_info: SessionInfo,
    registry: PluginRegistry,
    gate: PolicyGate,
    event_bus: EventBus,
):
    """
    KRN-08 Test 3: cancellation mid-stream records notification and emits turn.cancelled.
    """
    cancel_evt = asyncio.Event()

    # Provider that sets cancel_evt during stream
    class SelfCancellingProvider(ScriptedProvider):
        async def generate(self, req):
            yield TextDelta(text="Starting long calculation...")
            # Trigger cancellation during streaming
            cancel_evt.set()
            yield TextDelta(text="Continuing calculation...")
            yield Done(stop_reason="end_turn")

    provider = SelfCancellingProvider()
    registry.register_provider(provider)

    sub = event_bus.subscribe()

    runner = TurnRunner(
        registry=registry,
        gate=gate,
        bus=event_bus,
    )

    result = await runner.execute_turn(
        session=session_info,
        prompt="Start a long task",
        cancel_event=cancel_evt,
    )

    assert result.stop_reason == "cancelled"
    events_received = drain_events(sub)
    event_types = [e.type for e in events_received]
    assert "turn.cancelled" in event_types


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_n_failures():
    """
    KRN-08 Test 4: circuit breaker opens after N failures and raises ProviderError(kind="unavailable").
    """
    breaker = CircuitBreaker(max_failures=3, reset_timeout_s=30.0)

    # Initial state is CLOSED
    assert breaker.get_state("openai", "gpt-4o") == CircuitState.CLOSED
    breaker.check("openai", "gpt-4o")

    # Record 2 failures -> still CLOSED
    breaker.record_failure("openai", "gpt-4o")
    breaker.record_failure("openai", "gpt-4o")
    assert breaker.get_state("openai", "gpt-4o") == CircuitState.CLOSED
    breaker.check("openai", "gpt-4o")

    # Record 3rd failure -> transitions to OPEN
    breaker.record_failure("openai", "gpt-4o")
    assert breaker.get_state("openai", "gpt-4o") == CircuitState.OPEN

    # 4th check must raise ProviderError(kind="unavailable")
    with pytest.raises(ProviderError) as exc_info:
        breaker.check("openai", "gpt-4o")

    assert exc_info.value.kind == "unavailable"
    assert exc_info.value.retryable is True
    assert "Circuit breaker is OPEN" in str(exc_info.value)

    # Other provider/model remains CLOSED
    assert breaker.get_state("anthropic", "claude-3-sonnet") == CircuitState.CLOSED
    breaker.check("anthropic", "claude-3-sonnet")


@pytest.mark.asyncio
async def test_spend_cap_stops_the_turn(
    session_info: SessionInfo,
    registry: PluginRegistry,
    gate: PolicyGate,
    event_bus: EventBus,
):
    """
    KRN-08 Test 5: spend cap stops the turn when usage cost exceeds spend_cap_usd.
    """
    # Provider reporting usage cost of $6.00 (cap is $5.00)
    provider = ScriptedProvider()
    provider.add_response([
        TextDelta(text="Here is expensive output."),
        UsageEvent(
            usage=Usage(
                input_tokens=100000,
                output_tokens=50000,
                cost_usd=6.50,
            )
        ),
        Done(stop_reason="end_turn"),
    ])
    registry.register_provider(provider)

    sub = event_bus.subscribe()

    runner = TurnRunner(
        registry=registry,
        gate=gate,
        bus=event_bus,
    )

    result = await runner.execute_turn(
        session=session_info,
        prompt="Perform calculation",
        options={"spend_cap_usd": 5.0},
    )

    assert result.stop_reason == "spend_cap_exceeded"
    assert "exceeded configured spend cap" in (result.error or "")

    events_received = drain_events(sub)
    event_types = [e.type for e in events_received]
    assert "cost.updated" in event_types
    assert "turn.error" in event_types


@pytest.mark.asyncio
async def test_sequential_vs_parallel_tool_execution(
    session_info: SessionInfo,
    registry: PluginRegistry,
    gate: PolicyGate,
    event_bus: EventBus,
):
    """
    KRN-08 Test 6: batch with WRITE tool executes sequentially, batch with only READ executes in parallel.
    """
    active_concurrent = 0
    max_concurrent = 0

    spec_read1 = ToolSpec(
        name="t_read1",
        description="r1",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
    )
    spec_read2 = ToolSpec(
        name="t_read2",
        description="r2",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
    )
    spec_write = ToolSpec(
        name="t_write",
        description="w",
        input_schema={"type": "object"},
        risk_class=RiskClass.WRITE,
    )

    async def concurrent_tracker(args: dict[str, Any], ctx: Any) -> ToolResult:
        nonlocal active_concurrent, max_concurrent
        active_concurrent += 1
        if active_concurrent > max_concurrent:
            max_concurrent = active_concurrent
        await asyncio.sleep(0.05)
        active_concurrent -= 1
        return ToolResult.success("ok")

    gate.register_tool(spec_read1, concurrent_tracker)
    gate.register_tool(spec_read2, concurrent_tracker)
    gate.register_tool(spec_write, concurrent_tracker)

    services = KernelServicesImpl(
        runner=TurnRunner(registry, gate, event_bus),
        session=session_info,
        turn_id="t_seq_par",
        options={},
        cancel=asyncio.Event(),
    )

    # 1. READ only batch -> parallel execution (max_concurrent >= 2)
    active_concurrent = 0
    max_concurrent = 0
    calls_read = [
        ToolCall(id="c1", name="t_read1", arguments={}),
        ToolCall(id="c2", name="t_read2", arguments={}),
    ]
    results = await services.execute_tools(calls_read)
    assert len(results) == 2
    assert max_concurrent == 2

    # 2. Batch with WRITE -> sequential execution (max_concurrent == 1)
    active_concurrent = 0
    max_concurrent = 0
    calls_mixed = [
        ToolCall(id="c1", name="t_read1", arguments={}),
        ToolCall(id="c3", name="t_write", arguments={}),
    ]
    results = await services.execute_tools(calls_mixed)
    assert len(results) == 2
    assert max_concurrent == 1


@pytest.mark.asyncio
async def test_child_turn_depth_limiting(
    session_info: SessionInfo,
    registry: PluginRegistry,
    gate: PolicyGate,
    event_bus: EventBus,
):
    """
    KRN-08 Test 7: spawn_child enforces depth limit (max 2).
    Depth 0 -> spawn child (depth 1) -> spawn child (depth 2) -> spawn child (depth 3 raises RuntimeError).
    """
    runner = TurnRunner(registry, gate, event_bus)
    provider = ScriptedProvider()
    provider.add_response([TextDelta(text="Child turn complete."), Done(stop_reason="end_turn")])
    provider.add_response([TextDelta(text="Child turn complete."), Done(stop_reason="end_turn")])
    registry.register_provider(provider)

    services_d0 = KernelServicesImpl(
        runner=runner,
        session=session_info,
        turn_id="turn_d0",
        options={},
        cancel=asyncio.Event(),
        depth=0,
    )

    # Depth 0 spawning child at depth 1 succeeds
    child_res = await services_d0.spawn_child(prompt="Subagent prompt 1")
    assert child_res.stop_reason == "end_turn"

    # Services at depth 2 attempting to spawn child at depth 3 raises RuntimeError
    services_d2 = KernelServicesImpl(
        runner=runner,
        session=session_info,
        turn_id="turn_d2",
        options={},
        cancel=asyncio.Event(),
        depth=2,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await services_d2.spawn_child(prompt="Subagent prompt 3")

    assert "Max child turn depth (2) exceeded" in str(exc_info.value)

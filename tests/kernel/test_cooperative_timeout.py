"""WS-03a: cooperative tool timeout — signal, grace, then cancel, and stop the children.

Before this, step 7 was `asyncio.timeout(spec.timeout_s)` around the handler: on expiry the handler
coroutine was cancelled abruptly, whatever it had already streamed was discarded, and a subprocess it
had spawned kept running with the pipe open (the executor's `Process` is not the handler's cancel
scope). The model got `Tool 'x' timed out after 600s` and nothing else — not even partial output.

Now: the handler is signalled through `ctx.cancel_event` and gets `kernel.limits.tool_grace_s` to
return something useful; only then is the task cancelled. Anything registered with
`ctx.track_process(...)` is terminated either way, and the streamed deltas become the partial output
the model is shown.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.sdk.types import RiskClass, TextBlock, ToolCall, ToolResult, ToolSpec

TIMEOUT_S = 0.3
GRACE_S = 0.2


def _spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"test tool {name}",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk_class=RiskClass.READ,
        capabilities=frozenset(),
        timeout_s=TIMEOUT_S,
    )


@pytest.fixture
def gate(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings.json")
    settings.set("kernel.limits.tool_grace_s", GRACE_S)
    secrets = Secrets(tmp_path / "secrets.json")
    registry = PluginRegistry(workspace=workspace, settings=settings, secrets=secrets, bus=bus)
    gate = PolicyGate(
        registry=registry,
        jail=WorkspaceJail(workspace),
        settings=settings,
        secrets=secrets,
        approvals=ApprovalBroker(bus=bus, timeout_s=1.0),
        bus=bus,
    )
    return gate, registry, settings


@pytest.mark.asyncio
async def test_a_stuck_handler_is_cancelled_after_the_grace_period(gate):
    g, registry, _settings = gate
    cancelled = {"seen": False}

    async def stuck(args, ctx):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled["seen"] = True
            raise
        return ToolResult.text("never reached")

    registry._register_tool(_spec("stuck"), stuck, plugin_name="test_timeout")
    result = await g.execute(ToolCall(id="c1", name="stuck", arguments={}), session_id="s1")

    assert result.is_error is True
    assert f"exceeded {TIMEOUT_S:g}s" in result.output
    assert "cancelled" in result.output
    assert cancelled["seen"] is True, "the task must actually be cancelled, not abandoned"
    assert result.metadata["timed_out"] is True


@pytest.mark.asyncio
async def test_partial_output_reaches_the_model(gate):
    g, registry, _settings = gate

    async def chatty(args, ctx):
        ctx.emit_output_delta("stdout", "line one\n")
        ctx.emit_output_delta("stdout", "line two\n")
        await asyncio.sleep(30)
        return ToolResult.text("never")

    registry._register_tool(_spec("chatty"), chatty, plugin_name="test_partial")
    result = await g.execute(ToolCall(id="c1", name="chatty", arguments={}), session_id="s1")

    assert "line one" in result.output and "line two" in result.output
    assert result.metadata["partial_output"] is True


@pytest.mark.asyncio
async def test_a_handler_that_responds_to_the_signal_keeps_its_answer(gate):
    """The whole reason the cancel signal is public: a well-behaved tool returns *something*."""
    g, registry, _settings = gate

    async def well_mannered(args, ctx):
        while not ctx.cancel_event.is_set():
            await asyncio.sleep(0.02)
        return ToolResult(content=[TextBlock(text="partial report from the tool itself")])

    registry._register_tool(_spec("polite"), well_mannered, plugin_name="test_grace")
    result = await g.execute(ToolCall(id="c1", name="polite", arguments={}), session_id="s1")

    assert "partial report from the tool itself" in result.output
    assert result.is_error is True, "an incomplete turn must not look like a success"
    assert f"exceeded {TIMEOUT_S:g}s" in result.output


@pytest.mark.asyncio
async def test_tracked_child_process_is_terminated_with_the_call(gate):
    """R40: a timed-out `run_command` used to leak its subprocess past the turn."""
    g, registry, _settings = gate
    spawned: dict = {}

    async def spawner(args, ctx):
        proc = await asyncio.create_subprocess_exec(
            "sleep", "30", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        spawned["proc"] = proc
        ctx.track_process(proc)
        await asyncio.sleep(30)
        return ToolResult.text("never")

    registry._register_tool(_spec("spawner"), spawner, plugin_name="test_children")
    result = await g.execute(ToolCall(id="c1", name="spawner", arguments={}), session_id="s1")

    assert result.is_error is True
    proc = spawned["proc"]
    for _ in range(50):
        if proc.returncode is not None:
            break
        await asyncio.sleep(0.02)
    assert proc.returncode is not None, "the child outlived the tool call — the timeout leaked a process"


@pytest.mark.asyncio
async def test_deadline_is_set_during_the_call_and_cleared_after(gate):
    g, registry, _settings = gate
    seen: dict = {}

    async def peek(args, ctx):
        seen["deadline"] = ctx.deadline
        seen["cancelled"] = ctx.cancel_event.is_set()
        ctx.emit_output_delta("stdout", "buffered but returned in time")
        return ToolResult.text("fine")

    registry._register_tool(_spec("peek"), peek, plugin_name="test_deadline")
    result = await g.execute(ToolCall(id="c1", name="peek", arguments={}), session_id="s1")

    assert seen["deadline"] is not None and seen["cancelled"] is False
    assert result.output == "fine"
    assert "timed_out" not in result.metadata, "a call that finished must not look like a timeout"


@pytest.mark.asyncio
async def test_a_fast_call_does_not_leak_its_streamed_output_into_the_next_one(gate):
    """The buffer exists for the timeout path; it must not grow across calls."""
    g, registry, _settings = gate
    buffers: list[int] = []

    async def chatty(args, ctx):
        ctx.emit_output_delta("stdout", "x" * 500)
        buffers.append(len(ctx._output_buffer))
        return ToolResult.text("ok")

    registry._register_tool(_spec("buffer"), chatty, plugin_name="test_buffer")
    for i in range(3):
        await g.execute(ToolCall(id=f"c{i}", name="buffer", arguments={}), session_id="s1")
    assert buffers == [1, 1, 1], "each call buffers its own deltas; the gate drops them when the call succeeds"

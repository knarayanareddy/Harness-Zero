"""WS-03a: `TOOL_RESULT_FINALIZE` — the seam where size guards belong.

The gate truncates at `kernel.limits.tool_result_bytes` and keeps the *head*, so a 200 KB
`pytest` output reaches the model with the failure summary (its tail) cut off. Any guard that wants
to decide what the model reads has to run after the handler produced a result and *before* that
truncation — which is what this event is for. POST_TOOL_USE (step 8) is too early: its appends are
then truncated, and its `block` is too blunt for "show less, but show the right part".
"""

from __future__ import annotations

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.sdk.types import (
    HookEvent,
    HookOutcome,
    RiskClass,
    TextBlock,
    ToolCall,
    ToolResult,
    ToolSpec,
)


def _spec(name: str, handler_note: str = "ok") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"test tool {name}",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk_class=RiskClass.READ,
        capabilities=frozenset(),
    )


class _Hook:
    """Minimal Hook double: records what it saw, returns what it was told to."""

    def __init__(self, name: str, action: str = "continue", text: str | None = None, order: int = 500, raises: bool = False):
        self.name = name
        self.events = frozenset({HookEvent.TOOL_RESULT_FINALIZE})
        self.matcher = None
        self.timeout_s = 5.0
        self.order = order
        self.action = action
        self.text = text
        self.raises = raises
        self.seen: list[str] = []

    async def on(self, event, payload, ctx) -> HookOutcome:
        result = payload["result"]
        self.seen.append(result.output)
        if self.raises:
            raise RuntimeError("hook exploded")
        if self.action == "mutate" and self.text is not None:
            return HookOutcome(
                action="mutate",
                result=ToolResult(
                    content=[TextBlock(text=self.text)],
                    is_error=result.is_error,
                    metadata={**result.metadata, "mutated_by": self.name},
                ),
            )
        return HookOutcome(action="continue")


@pytest.fixture
def gate(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings.json")
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
    return gate, registry, settings, bus


@pytest.mark.asyncio
async def test_finalize_hook_can_replace_the_result_the_model_reads(gate):
    g, registry, _settings, _bus = gate
    big = "x" * 200_000
    registry._register_tool(
        _spec("big"), lambda args, ctx: _returns(big), plugin_name="test_finalize"
    )
    hook = _Hook("shrinker", action="mutate", text="HEAD … TAIL")
    registry._register_hook(hook, "test_finalize")

    result = await g.execute(ToolCall(id="c1", name="big", arguments={}), session_id="s1")
    assert hook.seen == [big], "the hook must see the handler's full result, not a truncated one"
    assert result.output == "HEAD … TAIL"
    assert result.metadata["mutated_by"] == "shrinker"


@pytest.mark.asyncio
async def test_finalize_runs_before_truncation_so_the_tail_survives(gate):
    """The point of the whole exercise: a tail-preserving prune beats head-preserving truncation."""
    g, registry, settings, _bus = gate
    settings.set("kernel.limits.tool_result_bytes", 4096)
    tail_marker = "FAILED tests/test_x.py::test_y"
    payload = ("log line\n" * 40_000) + tail_marker
    registry._register_tool(_spec("noisy"), lambda args, ctx: _returns(payload), plugin_name="test_tail")

    # without the guard the tail is gone
    plain = await g.execute(ToolCall(id="c1", name="noisy", arguments={}), session_id="s1")
    assert tail_marker not in plain.output
    assert "[TRUNCATED DUE TO SIZE LIMIT]" in plain.output

    # a finalize hook that keeps the tail is respected, and the backstop still caps the size
    registry._register_hook(_Hook("keep_tail", action="mutate", text=tail_marker), "test_tail")
    guarded = await g.execute(ToolCall(id="c2", name="noisy", arguments={}), session_id="s1")
    assert guarded.output.endswith(tail_marker)
    assert guarded.metadata.get("mutated_by") == "keep_tail"


@pytest.mark.asyncio
async def test_hooks_at_this_event_run_in_order(gate):
    g, registry, _settings, _bus = gate
    registry._register_tool(_spec("seq"), lambda args, ctx: _returns("start"), plugin_name="test_order")
    early = _Hook("early", action="mutate", text="early", order=100)
    late = _Hook("late", action="mutate", text="late(saw: {seen})", order=900)

    async def late_on(event, payload, ctx):
        late.seen.append(payload["result"].output)
        return HookOutcome(action="mutate", result=ToolResult.text(f"late(saw: {payload['result'].output})"))

    late.on = late_on  # type: ignore[method-assign]
    registry._register_hook(late, "test_order")
    registry._register_hook(early, "test_order")

    result = await g.execute(ToolCall(id="c1", name="seq", arguments={}), session_id="s1")
    assert result.output == "late(saw: early)", "order 900 must run after order 100"


@pytest.mark.asyncio
async def test_a_broken_finalize_hook_cannot_break_the_call(gate):
    g, registry, _settings, _bus = gate
    registry._register_tool(_spec("fragile"), lambda args, ctx: _returns("payload"), plugin_name="test_broken")
    registry._register_hook(_Hook("boom", raises=True), "test_broken")

    result = await g.execute(ToolCall(id="c1", name="fragile", arguments={}), session_id="s1")
    assert result.output == "payload"
    assert result.is_error is False, "a failing guard is a missing guard, not a failed tool call"


@pytest.mark.asyncio
async def test_turn_state_is_shared_per_turn_not_globally(gate):
    """Repeat detection and the read ledger live in `turn_state`; two turns must not see each other."""
    g, registry, _settings, _bus = gate
    seen: list[dict] = []

    class _Memo:
        name = "memo"
        events = frozenset({HookEvent.TOOL_RESULT_FINALIZE})
        matcher = None
        timeout_s = 5.0
        order = 500

        async def on(self, event, payload, ctx):
            seen.append(ctx.turn_state)
            ctx.turn_state.setdefault("calls", []).append(payload["call"].id)
            return HookOutcome(action="continue")

    registry._register_tool(_spec("memo"), lambda args, ctx: _returns("ok"), plugin_name="test_turn_state")
    registry._register_hook(_Memo(), "test_turn_state")

    state_a: dict = {}
    await g.execute(ToolCall(id="a1", name="memo", arguments={}), session_id="s1", turn_state=state_a)
    await g.execute(ToolCall(id="b1", name="memo", arguments={}), session_id="s1", turn_state={})
    assert state_a["calls"] == ["a1"], "a turn's scratch must not leak into the next turn"
    assert seen[0] is state_a


async def _returns(text: str) -> ToolResult:
    return ToolResult.text(text)

"""
Regression test: a model response that streams thinking deltas must complete the turn.

Before the fix, ``TurnRunner`` built ``ThinkingBlock(thinking=...)`` while the dataclass field is
``text`` (sdk/types.py), and every consumer (single_loop / architect_editor strategies, the
Anthropic provider, the runner's legacy loop) read ``block.thinking``. Net effect: any provider
that emitted a ``ThinkingDelta`` ended the turn in ``turn.error`` and the thinking never reached
``TurnResult.thinking``. Found by additions/scenarios/thinking_then_answer_web.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ModelInfo,
    Role,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    TurnResult,
    Usage,
    UsageEvent,
)


@pytest.mark.asyncio
async def test_thinking_deltas_complete_the_turn_and_are_not_fed_back_as_text(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    conf = tmp_path / ".harness"
    conf.mkdir()
    runtime = await bootstrap_kernel(workspace=ws, config_dir=conf)

    provider = ScriptedProvider(
        models=[ModelInfo(id="m", display_name="m", context_tokens=100000, input_usd_per_mtok=0, output_usd_per_mtok=0)]
    )
    provider.add_response([
        ThinkingDelta(text="The user wants a short answer."),
        TextDelta(text="Forty-two."),
        UsageEvent(usage=Usage(input_tokens=10, output_tokens=5)),
        Done(stop_reason="end_turn"),
    ])
    provider.add_response([
        TextDelta(text="Still forty-two."),
        UsageEvent(usage=Usage(input_tokens=20, output_tokens=5)),
        Done(stop_reason="end_turn"),
    ])
    runtime.registry.register_provider(provider, plugin_name="test_scripted")

    seen: list[str] = []
    sub = runtime.bus.subscribe()

    session = await runtime.session_manager.create_session()
    result = await runtime.runner.execute_turn(
        session=session.info,
        prompt="Give me a short answer.",
        options={"provider": "scripted", "model": "m"},
    )
    assert isinstance(result, TurnResult)
    assert result.output == "Forty-two."
    assert result.thinking == "The user wants a short answer."

    # Drain the bus: the turn must end in turn.completed, never turn.error.
    while not sub.queue.empty():
        seen.append(sub.queue.get_nowait().type)
    assert "turn.completed" in seen, seen
    assert "turn.error" not in seen, seen
    assert "model.thinking.delta" in seen, seen

    # Second turn: the model must never be shown its own reasoning as plain assistant prose.
    # (Known gap, not fixed here: TurnRunner.record() does not persist `thinking` into the
    # TranscriptRecord, so the ThinkingBlock restoration path in kernel/context.py is currently
    # unreachable and reloaded sessions lose the reasoning card. See FLOW_TEST_FINDINGS F-10.)
    await runtime.runner.execute_turn(
        session=session.info,
        prompt="Again?",
        options={"provider": "scripted", "model": "m"},
    )
    req = provider.requests[-1]
    assistant_msgs = [m for m in req.messages if m.role == Role.ASSISTANT]
    assert assistant_msgs, "history missing the first assistant message"
    first = assistant_msgs[0]
    assert first.text() == "Forty-two."
    assert "The user wants a short answer." not in first.text()
    for b in first.content:
        if isinstance(b, ThinkingBlock):
            assert b.text == "The user wants a short answer."

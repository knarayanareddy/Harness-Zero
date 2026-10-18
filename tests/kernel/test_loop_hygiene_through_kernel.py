"""WS-03: loop hygiene, end to end through the real kernel.

The kernel-seam tests (`test_tool_result_finalize_hook.py`, `test_cooperative_timeout.py`) prove the
plumbing exists. This file proves the *guards* change what the model reads — through
registry → gate → hooks → provider request, with a booted kernel and the mock settings — because the
failure mode this work targets is precisely "the unit works, the loop does not".
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ModelInfo,
    RiskClass,
    TextBlock,
    TextDelta,
    ToolCallEnd,
    ToolResult,
    ToolResultBlock,
    ToolSpec,
)

MODEL = ModelInfo(
    id="scripted-v1",
    display_name="Scripted",
    context_tokens=128_000,
    input_usd_per_mtok=0.0,
    output_usd_per_mtok=0.0,
)
OVERRIDES = {"kernel.security.permission_mode": "allow"}

HEAD_NOISE = "warning: deprecated thing\n" * 200
TAIL_MARKER = "FAILED tests/test_x.py::test_the_thing_that_broke - assert 1 == 2"
HUGE = HEAD_NOISE + ("log line\n" * 20_000) + TAIL_MARKER


def _noisy_spec() -> ToolSpec:
    return ToolSpec(
        name="noisy_command",
        description="Emits a huge log ending in a failure summary",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk_class=RiskClass.READ,
        capabilities=frozenset(),
    )


async def _noisy_handler(args: dict, ctx) -> ToolResult:
    return ToolResult.text(HUGE)


def _drain(sub) -> list:
    out = []
    while not sub.empty():
        out.append(sub.get_nowait())
    return out


def _seen_tool_results(provider: ScriptedProvider, request_index: int) -> list[ToolResultBlock]:
    """Tool results in the request at `request_index` (pass -1 for the last one)."""
    msgs = provider.requests[request_index].messages
    return [b for m in msgs for b in m.content if isinstance(b, ToolResultBlock)]


def _text_of(block: ToolResultBlock) -> str:
    return "\n".join(c.text for c in block.content if isinstance(c, TextBlock))


async def _boot(tmp_path: Path, **extra):
    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()
    rt = await bootstrap_kernel(
        workspace=ws, config_dir=conf, overrides={**OVERRIDES, **extra}, resume_session=False
    )
    return rt, ws


@pytest.mark.asyncio
async def test_oversized_output_keeps_tail(tmp_path: Path):
    """The whole point of WS-03: a guard decides the cut, so the failure summary survives."""
    rt, ws = await _boot(tmp_path)
    try:
        rt.registry.register_tool(_noisy_spec(), _noisy_handler, plugin_name="test_hygiene")
        live = rt.bus.subscribe({"tool.result.pruned"}, interactive=False)
        provider = ScriptedProvider(models=[MODEL])
        provider.add_response([ToolCallEnd(id="c1", name="noisy_command", arguments={}), Done(stop_reason="tool_use")])
        provider.add_response([TextDelta(text="Fixed."), Done(stop_reason="end_turn")])
        rt.registry.register_provider(provider, plugin_name="test_scripted")

        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="run the tests", options={"provider": "scripted", "model": MODEL.id}
        )

        blocks = _seen_tool_results(provider, -1)
        text = _text_of(blocks[-1])
        assert TAIL_MARKER in text, "the tail was cut again — the guard did not run"
        assert "[... " in text and " chars omitted (" in text, text[-400:]
        assert "full output: .harness/sessions/" in text, "the notice must point at the spool file"
        assert "TRUNCATED DUE TO SIZE LIMIT" not in text, "the guard should have made truncation unnecessary"

        # and the full output really is on disk, readable by the tool the notice names
        spool = next((ws / ".harness" / "sessions").glob("*/tool-output/c1.txt"))
        assert spool.read_text(encoding="utf-8") == HUGE
        events = _drain(live)
        assert [e.type for e in events] == ["tool.result.pruned"], events
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_turning_the_pruner_off_brings_back_head_only_truncation(tmp_path: Path):
    """The teeth of WS-03: without the guard the tail is gone. (Inverting `enabled` must break the
    test above — that is what makes the guard load-bearing rather than decorative.)"""
    rt, _ws = await _boot(tmp_path, **{"plugins.hook-tool-result-pruner.enabled": False})
    try:
        rt.registry.register_tool(_noisy_spec(), _noisy_handler, plugin_name="test_hygiene")
        live = rt.bus.subscribe({"tool.result.pruned"}, interactive=False)
        provider = ScriptedProvider(models=[MODEL])
        provider.add_response([ToolCallEnd(id="c1", name="noisy_command", arguments={}), Done(stop_reason="tool_use")])
        provider.add_response([TextDelta(text="Ok."), Done(stop_reason="end_turn")])
        rt.registry.register_provider(provider, plugin_name="test_scripted")

        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="run the tests", options={"provider": "scripted", "model": MODEL.id}
        )
        text = _text_of(_seen_tool_results(provider, -1)[-1])
        assert TAIL_MARKER not in text
        assert "[... " not in text
        assert "TRUNCATED DUE TO SIZE LIMIT" in text
        assert _drain(live) == [], "the pruner is off: it must not announce a pruning it did not do"
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_third_identical_call_is_pointed_out(tmp_path: Path):
    rt, _ws = await _boot(tmp_path)
    try:
        calls: list[int] = []

        async def ping(args: dict, ctx) -> ToolResult:
            calls.append(1)
            return ToolResult.text("pong")

        rt.registry.register_tool(
            ToolSpec(
                name="ping",
                description="Idempotent ping",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                risk_class=RiskClass.READ,
                capabilities=frozenset(),
            ),
            ping,
            plugin_name="test_repeat",
        )
        live = rt.bus.subscribe({"tool.repeat_detected"}, interactive=False)
        provider = ScriptedProvider(models=[MODEL])
        for i in (1, 2, 3):
            provider.add_response([ToolCallEnd(id=f"c{i}", name="ping", arguments={}), Done(stop_reason="tool_use")])
        provider.add_response([TextDelta(text="Stopping."), Done(stop_reason="end_turn")])
        rt.registry.register_provider(provider, plugin_name="test_scripted")

        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="ping it", options={"provider": "scripted", "model": "scripted-v1"}
        )

        assert len(calls) == 3
        first = _text_of(_seen_tool_results(provider, 1)[-1])
        assert "identical call" not in first, "the first repeat must not nag"
        third = _text_of(_seen_tool_results(provider, 3)[-1])
        assert third.startswith("pong"), third
        assert (
            "Note: this is the 3rd identical call to ping with the same arguments in this turn. "
            "The result has not changed."
        ) in third, third
        repeats = _drain(live)
        assert [e.type for e in repeats] == ["tool.repeat_detected"], repeats
        assert repeats[0].data["count"] == 3
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_edit_without_read_is_blocked_until_the_file_is_read(tmp_path: Path):
    rt, ws = await _boot(tmp_path)
    try:
        (ws / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
        diff = "<<<<<<< SEARCH\ndef greet():\n    return 'hi'\n=======\ndef hello():\n    return 'hi'\n>>>>>>> REPLACE\n"
        provider = ScriptedProvider(models=[MODEL])
        provider.add_response([ToolCallEnd(id="c1", name="edit_file", arguments={"file_path": "app.py", "diff": diff}), Done(stop_reason="tool_use")])
        provider.add_response([ToolCallEnd(id="c2", name="view_file", arguments={"file_path": "app.py"}), Done(stop_reason="tool_use")])
        provider.add_response([ToolCallEnd(id="c3", name="edit_file", arguments={"file_path": "app.py", "diff": diff}), Done(stop_reason="tool_use")])
        provider.add_response([TextDelta(text="Renamed."), Done(stop_reason="end_turn")])
        rt.registry.register_provider(provider, plugin_name="test_scripted")

        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="rename greet", options={"provider": "scripted", "model": "scripted-v1"}
        )

        blocked = _seen_tool_results(provider, -1)[0]
        assert blocked.is_error
        assert "edit_file blocked: app.py has not been read in this session. Read it first." in _text_of(blocked)
        assert "Successfully applied" not in _text_of(blocked), "a blocked edit must not have run"

        after_read = _seen_tool_results(provider, -1)[-1]
        assert not after_read.is_error, _text_of(after_read)
        final = (ws / "app.py").read_text(encoding="utf-8")
        assert final.count("def hello()") == 1 and "def greet" not in final
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_a_stuck_tool_returns_partial_output_instead_of_nothing(tmp_path: Path):
    rt, _ws = await _boot(tmp_path, **{"kernel.limits.tool_grace_s": 0.2})
    try:
        async def hang(args: dict, ctx) -> ToolResult:
            ctx.emit_output_delta("stdout", "progress: 42% done\n")
            await asyncio.sleep(30)
            return ToolResult.text("never")

        rt.registry.register_tool(
            ToolSpec(
                name="slow_thing",
                description="Hangs",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                risk_class=RiskClass.READ,
                capabilities=frozenset(),
                timeout_s=0.3,
            ),
            hang,
            plugin_name="test_timeout_e2e",
        )
        provider = ScriptedProvider(models=[MODEL])
        provider.add_response([ToolCallEnd(id="c1", name="slow_thing", arguments={}), Done(stop_reason="tool_use")])
        provider.add_response([TextDelta(text="Understood."), Done(stop_reason="end_turn")])
        rt.registry.register_provider(provider, plugin_name="test_scripted")

        session = await rt.session_manager.create_session()
        await rt.runner.execute_turn(
            session=session.info, prompt="do the slow thing", options={"provider": "scripted", "model": "scripted-v1"}
        )

        block = _seen_tool_results(provider, -1)[-1]
        text = _text_of(block)
        assert block.is_error, "a timed-out call must not be recorded as a success"
        assert "exceeded 0.3s" in text, text
        assert "progress: 42% done" in text, "the partial output is the whole reason to be polite about timeouts"
    finally:
        await rt.registry.unload_all()

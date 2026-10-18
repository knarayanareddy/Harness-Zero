"""WS-03 loop-hygiene hooks: conformance, declared settings, and the exact strings the model reads.

The end-to-end behaviour lives in `tests/kernel/test_loop_hygiene_through_kernel.py`; this file pins
the plugin contract around it — the kit invariants, the schema/namespace/keys each plugin declares, and
the wording in the model's context (the strings are the product: a reworded notice is a behaviour
change, so it must be a deliberate one).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.kernel.catalog import README_SECTIONS
from harness.plugins.hooks.read_before_edit.hook import (
    BLOCK_MESSAGE,
    DETAIL_STALE,
    DETAIL_UNREAD,
    ReadBeforeEditHook,
    ReadBeforeEditSettings,
)
from harness.plugins.hooks.repeat_call_reminder.hook import REPEAT_NOTE, RepeatCallReminderHook, RepeatSettings, ordinal
from harness.plugins.hooks.tool_result_pruner.hook import PRUNE_NOTICE, PrunerSettings, ToolResultPrunerHook
from harness.plugins.hooks.tool_result_spill.hook import SPILL_NOTE, SpillSettings, ToolResultSpillHook
from harness.sdk.testing.kits import HookKit
from harness.sdk.types import HookEvent, ToolCall, ToolResult

PLUGIN_DIRS = {
    "tool_result_pruner": "hook-tool-result-pruner",
    "tool_result_spill": "hook-tool-result-spill",
    "repeat_call_reminder": "hook-repeat-call-reminder",
    "read_before_edit": "hook-read-before-edit",
}


def _plugin_dir(mod: str) -> Path:
    return Path(__file__).resolve().parents[2] / "harness" / "plugins" / "hooks" / mod


@pytest.mark.parametrize("mod", sorted(PLUGIN_DIRS))
@pytest.mark.asyncio
async def test_hookkit_conformance(mod: str):
    """Each hook returns a valid HookOutcome even for the kit's empty payload."""
    hook = {
        "tool_result_pruner": ToolResultPrunerHook(PrunerSettings()),
        "tool_result_spill": ToolResultSpillHook(SpillSettings(), Path("/tmp")),
        "repeat_call_reminder": RepeatCallReminderHook(RepeatSettings()),
        "read_before_edit": ReadBeforeEditHook(ReadBeforeEditSettings(), Path("/tmp")),
    }[mod]
    ctx = MagicMock()
    ctx.workspace = Path("/tmp")
    ctx.turn_state = {}
    await HookKit().test_hook_outcome_action(hook, ctx)
    assert hook.events and all(isinstance(e, HookEvent) for e in hook.events)
    assert isinstance(hook.order, int)


@pytest.mark.parametrize("mod", sorted(PLUGIN_DIRS))
def test_readme_has_the_five_contract_sections(mod: str):
    text = (_plugin_dir(mod) / "README.md").read_text(encoding="utf-8")
    missing = [s for s in README_SECTIONS if f"## {s}" not in text]
    assert missing == [], f"{mod}/README.md missing sections: {missing}"


@pytest.mark.parametrize("mod", sorted(PLUGIN_DIRS))
def test_settings_are_declared_under_the_manifest_namespace(mod: str):
    """The manifest name, the schema namespace and the keys `plugin.py` reads must agree.

    A key read but not declared is the failure WS-02 was written to catch (it silently ignores what
    an operator sets); a namespace that does not match the plugin name resolves to a dead prefix.
    """
    d = _plugin_dir(mod)
    manifest = (d / "harness_plugin.toml").read_text(encoding="utf-8")
    name = re.search(r'name = "([^"]+)"', manifest).group(1)
    assert name == PLUGIN_DIRS[mod], f"{mod}: manifest name {name!r} != {PLUGIN_DIRS[mod]!r}"

    schema = json.loads((d / "settings.schema.json").read_text(encoding="utf-8"))
    assert schema["x-namespace"] == f"plugins.{name}"
    declared = set(schema["properties"])

    reads = set(re.findall(r's\.get\("([a-z_][a-z0-9_]*)"', (d / "plugin.py").read_text(encoding="utf-8")))
    undeclared = sorted(reads - declared)
    assert not undeclared, f"{mod} reads undeclared settings: {undeclared}"
    unused = sorted(declared - reads)
    assert not unused, f"{mod} declares settings nothing reads: {unused}"


def test_pruner_notice_is_the_documented_string():
    """The §1 wording, verbatim — the model's ability to reason about a cut depends on it."""
    text = "a" * 200 + "\n" + "b" * 300
    notice = PRUNE_NOTICE.format(omitted=1234, lines=57, suffix="; full output: .harness/sessions/s/tool-output/c1.txt")
    assert notice == (
        "\n\n[... 1234 chars omitted (57 lines); full output: "
        ".harness/sessions/s/tool-output/c1.txt ...]\n\n"
    )
    assert text  # the shape above is what gets spliced between head and tail


def test_spill_note_is_the_documented_string():
    note = SPILL_NOTE.format(relpath=".harness/sessions/abc/tool-output/c1.txt", n=91234)
    assert note == (
        "Full output saved to .harness/sessions/abc/tool-output/c1.txt (91234 chars). "
        "Use view_file with offset/limit to read more."
    )


def test_repeat_note_is_the_documented_string():
    assert ordinal(3) == "3rd" and ordinal(11) == "11th" and ordinal(21) == "21st"
    note = REPEAT_NOTE.format(ordinal="3rd", tool="run_command")
    assert note == (
        "Note: this is the 3rd identical call to run_command with the same arguments in this turn. "
        "The result has not changed. Consider a different approach or explain why repeating is needed."
    )


def test_read_before_edit_message_is_the_documented_string():
    stale = BLOCK_MESSAGE.format(tool="edit_file", detail=DETAIL_STALE.format(path="app.py", step=4))
    assert stale == (
        "edit_file blocked: app.py has not been read in this session "
        "(or changed since it was read at step 4). Read it first."
    )
    unread = BLOCK_MESSAGE.format(tool="edit_file", detail=DETAIL_UNREAD.format(path="app.py"))
    assert unread == "edit_file blocked: app.py has not been read in this session. Read it first."


# ---------------------------------------------------------------------------------- behaviours


@pytest.mark.asyncio
async def test_pruner_keeps_head_and_tail_and_reports_the_cut(tmp_path: Path):
    hook = ToolResultPrunerHook(PrunerSettings(threshold_chars=100, head_chars=40, tail_chars=30))
    body = ("head line\n" * 10) + ("filler\n" * 100) + ("tail line\n" * 5)
    result = ToolResult.text(body)
    outcome = await hook.on(
        HookEvent.TOOL_RESULT_FINALIZE,
        {"call": ToolCall(id="c1", name="pytest", arguments={}), "result": result, "session_id": "s"},
        MagicMock(turn_state={}),
    )
    assert outcome.action == "mutate"
    text = outcome.result.output
    assert text.startswith("head line\n")
    assert text.endswith("tail line\n")
    assert "tail line" in text and "chars omitted (" in text
    assert outcome.result.metadata["pruned"] is True
    # the replacement is always smaller than what it replaced
    assert len(text) < len(body)


@pytest.mark.asyncio
async def test_pruner_leaves_small_results_and_exempt_tools_alone(tmp_path: Path):
    hook = ToolResultPrunerHook(PrunerSettings(threshold_chars=1000))
    result = ToolResult.text("short")
    out = await hook.on(
        HookEvent.TOOL_RESULT_FINALIZE,
        {"call": ToolCall(id="c1", name="pytest", arguments={}), "result": result},
        MagicMock(turn_state={}),
    )
    assert out.action == "continue"
    big = ToolResult.text("x" * 5000)
    out = await hook.on(
        HookEvent.TOOL_RESULT_FINALIZE,
        {"call": ToolCall(id="c2", name="view_file", arguments={}), "result": big},
        MagicMock(turn_state={}),
    )
    assert out.action == "continue", "view_file output is an explicit range the model asked for"


@pytest.mark.asyncio
async def test_disabled_pruner_does_nothing(tmp_path: Path):
    """The teeth of the guard, at unit level: the kernel test asserts the consequence."""
    hook = ToolResultPrunerHook(PrunerSettings(enabled=False, threshold_chars=10))
    out = await hook.on(
        HookEvent.TOOL_RESULT_FINALIZE,
        {"call": ToolCall(id="c1", name="pytest", arguments={}), "result": ToolResult.text("x" * 5000)},
        MagicMock(turn_state={}),
    )
    assert out.action == "continue"


@pytest.mark.asyncio
async def test_spill_writes_the_full_output_and_caps_the_spool(tmp_path: Path):
    hook = ToolResultSpillHook(
        SpillSettings(threshold_chars=10, max_files_per_session=2), tmp_path
    )
    ctx = MagicMock(turn_state={}, session_id="sess1")
    body = "0123456789abcdefghij"
    for i in range(4):
        call = ToolCall(id=f"c{i}", name="pytest", arguments={})
        out = await hook.on(
            HookEvent.TOOL_RESULT_FINALIZE,
            {"call": call, "result": ToolResult.text(body + str(i))},
            ctx,
        )
        assert out.action == "mutate"
    spool = tmp_path / ".harness" / "sessions" / "sess1" / "tool-output"
    kept = sorted(p.name for p in spool.glob("*.txt"))
    assert len(kept) == 2 and "c3.txt" in kept, f"the spool must stay bounded with the current file kept: {kept}"
    assert (spool / "c3.txt").read_text(encoding="utf-8") == body + "3"
    assert "\n\nFull output saved to .harness/sessions/sess1/tool-output/c3.txt (21 chars)." in out.result.output
    # a body the pruner will shorten keeps an intact tail: the note is appended, not substituted
    assert out.result.output.endswith("\n\nFull output saved to .harness/sessions/sess1/tool-output/c3.txt (21 chars). Use view_file with offset/limit to read more.")
    assert out.result.metadata["spill_chars"] == len(body + "3")


@pytest.mark.asyncio
async def test_repeat_counter_resets_when_the_result_changes():
    hook = RepeatCallReminderHook(RepeatSettings(repeat_after=3))
    state: dict = {}
    ctx = MagicMock(turn_state=state, session_id="s")
    async def one(text: str, cid: str):
        return await hook.on(
            HookEvent.POST_TOOL_USE,
            {
                "call": ToolCall(id=cid, name="run_command", arguments={"command": "pytest"}),
                "result": ToolResult.text(text),
            },
            ctx,
        )

    same = "3 passed in 0.4s"
    assert (await one(same, "c1")).action == "continue"
    assert (await one(same, "c2")).action == "continue"
    nag = await one(same, "c3")
    assert "3rd identical call" in nag.additional_context
    assert state["repeat_calls"][next(iter(state["repeat_calls"]))]["count"] == 3
    # the same command after an edit prints something new ⇒ not a spin any more
    after = await one("4 passed in 0.4s", "c4")
    assert after.action == "continue"
    assert state["repeat_calls"][next(iter(state["repeat_calls"]))]["count"] == 1


@pytest.mark.asyncio
async def test_repeat_guard_can_hard_stop_a_loop():
    hook = RepeatCallReminderHook(RepeatSettings(repeat_after=2, block_after=3))
    ctx = MagicMock(turn_state={}, session_id="s")
    for i in range(1, 4):
        out = await hook.on(
            HookEvent.POST_TOOL_USE,
            {"call": ToolCall(id=f"c{i}", name="pytest", arguments={}), "result": ToolResult.text("same")},
            ctx,
        )
    assert out.action == "block", "block_after must convert the nag into a stop"
    assert "identical call" in out.reason


@pytest.mark.asyncio
async def test_read_ledger_ignores_failed_reads_and_the_pending_call(tmp_path: Path):
    hook = ReadBeforeEditHook(ReadBeforeEditSettings(), tmp_path)
    (tmp_path / "app.py").write_text("body\n", encoding="utf-8")
    call = ToolCall(id="c9", name="edit_file", arguments={"file_path": "app.py", "diff": "..."})
    # `timestamp` is when the record was written, i.e. just after the tool ran; staleness compares it
    # with the file's mtime, so the fixture has to use a plausible clock.
    now = time.time()

    async def decide(records):
        ctx = MagicMock(turn_state={"records": records}, session_id="s", workspace=tmp_path)
        return await hook.on(HookEvent.PRE_TOOL_USE, {"call": call, "spec": None}, ctx)

    # a read that failed is not a read
    out = await decide(
        [
            {"tool_calls": [{"id": "c1", "name": "view_file", "arguments": {"file_path": "app.py"}}],
             "tool_results": [{"tool_use_id": "c1", "is_error": True}],
             "timestamp": now},
        ]
    )
    assert out.action == "block"
    # a completed read is
    out = await decide(
        [
            {"tool_calls": [{"id": "c1", "name": "view_file", "arguments": {"file_path": "app.py"}}],
             "tool_results": [{"tool_use_id": "c1", "is_error": False}],
             "timestamp": now},
        ]
    )
    assert out.action == "continue"
    # the edit being attempted right now must not count as its own read
    out = await decide(
        [{"tool_calls": [{"id": "c9", "name": "edit_file", "arguments": {"file_path": "app.py"}}], "timestamp": now}]
    )
    assert out.action == "block"
    # mode=warn keeps the call and annotates it instead
    warned = ReadBeforeEditHook(ReadBeforeEditSettings(mode="warn"), tmp_path)
    out = await warned.on(
        HookEvent.PRE_TOOL_USE, {"call": call, "spec": None}, MagicMock(turn_state={"records": []}, session_id="s")
    )
    assert out.action == "continue" and "Warning: edit_file" in out.additional_context


@pytest.mark.asyncio
async def test_exempt_globs_are_matched_against_workspace_relative_paths(tmp_path: Path):
    hook = ReadBeforeEditHook(ReadBeforeEditSettings(exempt_globs=["vendor/*"]), tmp_path)
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("x\n", encoding="utf-8")
    out = await hook.on(
        HookEvent.PRE_TOOL_USE,
        {
            "call": ToolCall(id="c1", name="edit_file", arguments={"file_path": "vendor/lib.py", "diff": "..."}),
            "spec": None,
        },
        MagicMock(turn_state={"records": []}, session_id="s"),
    )
    assert out.action == "continue"


def test_finalizes_are_ordered_spill_then_pruner():
    """The two size guards cooperate through metadata, so their order is part of the contract."""
    assert ToolResultSpillHook(SpillSettings(), Path("/tmp")).order < ToolResultPrunerHook(
        PrunerSettings()
    ).order
    assert ToolResultSpillHook.name == "tool_result_spill"
    assert ToolResultPrunerHook.events == frozenset({HookEvent.TOOL_RESULT_FINALIZE})
    assert ToolResultPrunerHook.order == 900 and ToolResultSpillHook.order == 800

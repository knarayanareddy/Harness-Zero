"""T09/T10: exercise compaction with the bootstrapped kernel's actual contracts."""
from __future__ import annotations

import asyncio
import json

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.compaction import COMPACTION_SYSTEM_PROMPT
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, HookEvent, HookOutcome, ModelParams, TextDelta

SUMMARY = "## Goal\nKeep the objective.\n\n## Decisions\nKeep constraints.\n\n## State of work\nVerified.\n\n## Open items\nFinish."


def test_compaction_completion_schema_declares_its_committed_record():
    from harness.sdk.events import schema

    assert schema()["events"]["compaction.completed"]["carries_record"] is True


@pytest.mark.asyncio
async def test_uncommitted_summary_is_only_a_draft_not_a_completed_fact(tmp_path):
    from harness.kernel.bus import EventBus
    from harness.kernel.compaction import Compactor
    from harness.kernel.settings import SettingsStore

    bus = EventBus()
    sub = bus.subscribe()

    async def summarize(system, conversation):
        return SUMMARY

    assert await Compactor(SettingsStore(tmp_path / "settings.json"), bus).compact("draft", [], summarize) == SUMMARY
    while not sub.empty():
        assert sub.get_nowait().type != "compaction.completed"


async def boot(tmp_path, provider=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    rt = await bootstrap_kernel(workspace=workspace, config_dir=tmp_path / "config",
        overrides={"kernel.provider": "scripted", "kernel.model": "scripted-v1",
                   "kernel.context.budget_tokens": 1000}, resume_session=False)
    provider = provider or ScriptedProvider(
        responses=[[TextDelta(SUMMARY), Done(stop_reason="end_turn")],
                   [TextDelta("answer"), Done(stop_reason="end_turn")]], strict_exhaustion=True)
    rt.registry.register_provider(provider, plugin_name="test_scripted")
    return rt, provider


@pytest.mark.asyncio
async def test_threshold_uses_current_hooks_and_valid_summarizer_request(tmp_path):
    rt, provider = await boot(tmp_path)
    seen = []

    class CurrentHook:
        name = "compaction_contract_probe"
        events = frozenset({HookEvent.PRE_COMPACT})
        timeout_s = 1.0
        order = 500
        matcher = None

        async def on(self, event, payload, ctx):
            seen.append((event, payload, ctx))
            return HookOutcome()

    rt.registry._register_hook(CurrentHook(), "test_hook")
    try:
        session = await rt.session_manager.create_session()
        await session.log.append({"role": "user", "content": "prior objective " * 500})
        result = await rt.runner.execute_turn(session=session.info, prompt="continue",
            options={"provider": "scripted", "model": "scripted-v1"})
        assert result.error is None, result.error
        assert len(seen) == 1
        assert seen[0][0] == HookEvent.PRE_COMPACT
        assert seen[0][2].session_id == session.info.session_id
        assert seen[0][2].log is session.log
        assert seen[0][2].settings is rt.settings
        assert len(provider.requests) == 2
        summary_req = provider.requests[0]
        assert isinstance(summary_req.params, ModelParams)
        assert summary_req.tools == []
        assert COMPACTION_SYSTEM_PROMPT in summary_req.system[0].text
        assert "prior objective" in summary_req.messages[0].text()
        assert summary_req.to_dict()["params"] == summary_req.params.to_dict()
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_summary_is_durable_before_completion_and_used_after_restart(tmp_path):
    from harness.kernel.compaction import estimate_request_tokens
    from harness.kernel.context import ContextAssembler
    from harness.kernel.transcript import TranscriptLog
    from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore

    rt, provider = await boot(tmp_path)
    sub = rt.bus.subscribe()
    try:
        session = await rt.session_manager.create_session()
        await session.log.append({"role": "user", "content": "prior objective " * 500})
        await session.log.append({"role": "assistant", "content": "old answer " * 300})
        before = session.log.store.full_path.read_bytes()
        result = await rt.runner.execute_turn(session=session.info, prompt="current objective: preserve constraints",
            options={"provider": "scripted", "model": "scripted-v1"})
        assert result.error is None, result
        summaries = [r for r in await session.log.read() if r["kind"] == "compaction.summary"]
        assert len(summaries) == 1, "Generated summary must be persisted, not discarded"
        summary = summaries[0]
        assert summary["content"] == SUMMARY
        assert summary["metadata"]["covers_through_step"] == 2
        assert session.log.store.full_path.read_bytes().startswith(before)
        answer_req = provider.requests[-1]
        assert SUMMARY in answer_req.messages[0].text()
        assert [m.text() for m in answer_req.messages].count("current objective: preserve constraints") == 1
        assert not any("old answer " in m.text() for m in answer_req.messages)
        assert estimate_request_tokens(answer_req) <= rt.settings.get("kernel.context.budget_tokens")
        completed = []
        while not sub.empty():
            event = sub.get_nowait()
            if event.type == "compaction.completed":
                completed.append(event)
        assert len(completed) == 1 and completed[0].record is not None
        assert completed[0].record["step_index"] == summary["step_index"]

        restarted = TranscriptLog(JsonlTranscriptStore(session.log.session_dir))
        await restarted.load()
        req = await ContextAssembler(rt.registry, rt.settings).assemble(session_id=session.info.session_id,
            history_records=restarted.records, prompt="new prompt", model="scripted-v1")
        assert SUMMARY in req.messages[0].text()
        assert [m.text() for m in req.messages].count("current objective: preserve constraints") == 1
        assert req.messages[-1].text() == "new prompt"
        assert (await restarted.last_compaction())["step_index"] == summary["step_index"]
    finally:
        rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["hook_block", "hook_error", "hook_timeout", "provider_error", "provider_timeout", "empty", "incomplete", "malformed", "oversized"])
async def test_compaction_failure_is_bounded_typed_and_not_completed(tmp_path, failure):
    class HangingProvider(ScriptedProvider):
        closed = False

        async def generate(self, req):
            self.requests.append(req)
            try:
                await asyncio.Event().wait()
                yield Done(stop_reason="end_turn")
            finally:
                self.closed = True

    provider = HangingProvider() if failure == "provider_timeout" else ScriptedProvider(
        responses=[RuntimeError("provider broke")] if failure == "provider_error" else
                  [[Done(stop_reason="end_turn")]] if failure == "empty" else
                  [[TextDelta(SUMMARY)]] if failure == "incomplete" else
                  [[TextDelta("unsupported summary"), Done(stop_reason="end_turn")]] if failure == "malformed" else
                  [[TextDelta(SUMMARY + " inflated" * 1500), Done(stop_reason="end_turn")]] if failure == "oversized" else
                  [[TextDelta(SUMMARY), Done(stop_reason="end_turn")]], strict_exhaustion=True)
    rt, _ = await boot(tmp_path, provider)
    rt.settings.set("kernel.request_timeout_s", 1.0)
    sub = rt.bus.subscribe()

    class FailedHook:
        name = "failed_compaction_hook"
        events = frozenset({HookEvent.PRE_COMPACT})
        timeout_s = 0.01
        order = 500
        matcher = None

        async def on(self, event, payload, ctx):
            if failure == "hook_timeout":
                await asyncio.Event().wait()
            if failure == "hook_error":
                raise RuntimeError("hook broke")
            return HookOutcome(action="block", reason="do not compact")

    if failure.startswith("hook_"):
        rt.registry._register_hook(FailedHook(), "test_hook")
    task = None
    try:
        session = await rt.session_manager.create_session()
        await session.log.append({"role": "user", "content": "history " * 1000})
        events = []

        async def until(event_type):
            while True:
                event = await sub.get()
                events.append(event)
                if event.type == event_type:
                    return event

        task = asyncio.create_task(rt.runner.execute_turn(session=session.info, prompt="continue",
            options={"provider": "scripted", "model": "scripted-v1"}))
        await asyncio.wait_for(until("compaction.started"), timeout=10)
        # Bound the compaction failure, not unrelated checkpoint setup/STOP I/O.
        await asyncio.wait_for(until("turn.error"), timeout=4)
        result = await task
        assert result.error, result
        while not sub.empty():
            events.append(sub.get_nowait())
        assert not [e for e in events if e.type in {"compaction.completed", "turn.completed"}]
        errors = [e for e in events if e.type == "turn.error"]
        assert errors and errors[-1].data["kind"] == "compaction_error"
        assert errors[-1].data["retryable"] is False
        assert not [r for r in session.log.records if r["kind"] == "compaction.summary"]
        if failure.startswith("hook_"):
            assert provider.requests == []
        if failure == "provider_timeout":
            assert provider.closed
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_real_openai_adapter_sends_summarizer_request_to_strict_local_peer(tmp_path):
    import httpx

    from harness.plugins.providers.openai_compat.provider import OpenAICompatProvider

    requests, failures = [], []
    workers = set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        workers.add(task)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode().split("\r\n")
            if lines[0].startswith("GET /v1/models "):
                body = json.dumps({"data": [{"id": "scripted-v1"}]}).encode()
                mime = "application/json"
            else:
                assert lines[0] == "POST /v1/chat/completions HTTP/1.1"
                headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
                req = json.loads(await reader.readexactly(int(headers["Content-Length"])))
                assert req["model"] == "scripted-v1" and req["stream"] is True
                assert req["messages"][0]["role"] == "system"
                if not requests:
                    assert "tools" not in req
                    assert COMPACTION_SYSTEM_PROMPT in req["messages"][0]["content"]
                    assert "prior objective" in req["messages"][1]["content"]
                requests.append(req)
                text = SUMMARY if len(requests) == 1 else "wire answer"
                chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]}
                body = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
                mime = "text/event-stream"
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: {mime}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except Exception as error:
            failures.append(error)
        finally:
            writer.close()
            await writer.wait_closed()
            workers.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = httpx.AsyncClient(trust_env=False)
    provider = OpenAICompatProvider(name="scripted", base_url=f"http://127.0.0.1:{port}/v1", client=client)
    rt = None
    try:
        rt, _ = await boot(tmp_path, provider)
        session = await rt.session_manager.create_session()
        await session.log.append({"role": "user", "content": "prior objective " * 500})
        result = await asyncio.wait_for(rt.runner.execute_turn(session=session.info, prompt="continue",
            options={"provider": "scripted", "model": "scripted-v1"}), 10)
        assert not failures, failures
        assert result.error is None and result.output == "wire answer", result
        assert len(requests) == 2
        assert SUMMARY in requests[-1]["messages"][1]["content"]
        assert sum(m.get("content") == "continue" for m in requests[-1]["messages"]) == 1
        assert not any("prior objective " in str(m.get("content")) for m in requests[-1]["messages"])
    finally:
        if rt is not None:
            await rt.registry.unload_all()
        await client.aclose()
        server.close()
        await server.wait_closed()
        await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_during_summary_closes_provider_without_persisting_completion(tmp_path):
    class HeldProvider(ScriptedProvider):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.closed = False

        async def generate(self, req):
            self.requests.append(req)
            try:
                self.started.set()
                await asyncio.Event().wait()
                yield TextDelta(SUMMARY)
            finally:
                self.closed = True

    provider = HeldProvider()
    rt, _ = await boot(tmp_path, provider)
    sub = rt.bus.subscribe()
    task = None
    try:
        session = await rt.session_manager.create_session()
        await session.log.append({"role": "user", "content": "history " * 1000})
        task = asyncio.create_task(rt.runner.execute_turn(session=session.info, prompt="continue",
            options={"provider": "scripted", "model": "scripted-v1"}))
        await asyncio.wait_for(provider.started.wait(), 10)
        assert rt.runner.cancel_turn(session.info.session_id)

        async def cancelled():
            while True:
                event = await sub.get()
                assert event.type not in {"compaction.completed", "turn.completed"}
                if event.type == "turn.cancelled":
                    return

        await asyncio.wait_for(cancelled(), 1)
        result = await task
        assert result.stop_reason == "cancelled"
        assert provider.closed
        assert not [r for r in await session.log.read() if r["kind"] == "compaction.summary"]
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_multiple_compactions_keep_latest_summary_and_current_prompt_once(tmp_path):
    from harness.kernel.compaction import estimate_request_tokens

    summary2 = SUMMARY.replace("Keep the objective.", "Latest objective and constraints.")
    provider = ScriptedProvider(responses=[
        [TextDelta(SUMMARY), Done(stop_reason="end_turn")], [TextDelta("answer one"), Done(stop_reason="end_turn")],
        [TextDelta(summary2), Done(stop_reason="end_turn")], [TextDelta("answer two"), Done(stop_reason="end_turn")],
    ], strict_exhaustion=True)
    rt, _ = await boot(tmp_path, provider)
    try:
        session = await rt.session_manager.create_session()
        await session.log.append({"role": "user", "content": "original goal and constraints " * 500})
        for prompt in ("first current prompt", "second current prompt"):
            if provider.requests:
                await session.log.append({"role": "assistant", "content": "recent work " * 500})
            result = await rt.runner.execute_turn(session=session.info, prompt=prompt,
                options={"provider": "scripted", "model": "scripted-v1"})
            assert result.error is None, result
            assert estimate_request_tokens(provider.requests[-1]) <= 1000
        summaries = [r for r in await session.log.read() if r["kind"] == "compaction.summary"]
        assert len(summaries) == 2
        assert summaries[-1]["metadata"]["covers_through_step"] > summaries[0]["step_index"]
        assert SUMMARY in provider.requests[2].messages[0].text(), "Second summary must consume the first"
        texts = [m.text() for m in provider.requests[-1].messages]
        assert summary2 in texts[0]
        assert texts.count("second current prompt") == 1
        assert "first current prompt" not in texts
        assert provider.remaining == 0
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_summary_write_failure_keeps_history_and_never_reports_completion(tmp_path, monkeypatch):
    rt, provider = await boot(tmp_path)
    sub = rt.bus.subscribe()
    try:
        session = await rt.session_manager.create_session()
        await session.log.append({"role": "user", "content": "history " * 1000})
        original = session.log.store.append

        async def fail_summary(record):
            if record.get("kind") == "compaction.summary":
                raise OSError("synthetic summary append failure")
            return await original(record)

        monkeypatch.setattr(session.log.store, "append", fail_summary)
        result = await rt.runner.execute_turn(session=session.info, prompt="continue",
            options={"provider": "scripted", "model": "scripted-v1"})
        assert result.error and result.stop_reason == "transcript_append_failed"
        rows = await session.log.read()
        assert rows[0]["content"] == "history " * 1000
        assert not [r for r in rows if r["kind"] == "compaction.summary"]
        assert len(provider.requests) == 1
        while not sub.empty():
            assert sub.get_nowait().type not in {"compaction.completed", "turn.completed"}
    finally:
        rt.bus.unsubscribe(sub)
        await rt.registry.unload_all()


@pytest.mark.asyncio
async def test_unresolved_call_stays_outside_summary_coverage(tmp_path):
    from harness.kernel.compaction import Compactor
    from harness.kernel.context import derive_messages
    from harness.kernel.settings import SettingsStore
    from harness.kernel.transcript import TranscriptLog
    from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore
    from harness.sdk.types import ToolUseBlock

    settings = SettingsStore(tmp_path / "settings.json")
    settings.set("kernel.context.budget_tokens", 1000)
    log = TranscriptLog(JsonlTranscriptStore(tmp_path / "session"))
    await log.append({"role": "user", "content": "old objective " * 1000})
    await log.append({"role": "assistant", "tool_calls": [{"id": "pending", "name": "never_execute"}]})
    await log.append({"role": "user", "content": "current objective; do not execute pending work"})
    provider = ScriptedProvider(responses=[[TextDelta(SUMMARY), Done(stop_reason="end_turn")]], strict_exhaustion=True)
    assert await Compactor(settings).maybe_compact(session_id="test", blocks=derive_messages(log.records),
        provider=provider, model="scripted-v1", log=log)
    summary = await log.last_compaction()
    assert summary["metadata"]["covers_through_step"] == 1
    messages = derive_messages(await log.read())
    assert any(isinstance(b, ToolUseBlock) and b.id == "pending" for m in messages for b in m.content)
    assert messages[-1].text() == "current objective; do not execute pending work"
    assert log.dangling_tool_calls()[0]["call_id"] == "pending"
    assert "never_execute" not in provider.requests[0].messages[0].text()


@pytest.mark.asyncio
async def test_oversized_first_prompt_is_summarized_without_reinserting_raw_prompt(tmp_path):
    from harness.kernel.compaction import estimate_request_tokens
    from harness.kernel.context import ContextAssembler
    from harness.kernel.transcript import TranscriptLog
    from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore

    rt, provider = await boot(tmp_path)
    prompt = "current objective and constraints " * 1000
    try:
        session = await rt.session_manager.create_session()
        result = await rt.runner.execute_turn(session=session.info, prompt=prompt,
            options={"provider": "scripted", "model": "scripted-v1"})
        assert result.error is None, result
        summary = await session.log.last_compaction()
        rows = await session.log.read()
        original = next(r for r in rows if r["kind"] == "user.message")
        assert original["content"] == prompt
        assert summary["metadata"]["summarized_prompt_step"] == original["step_index"]
        assert "current objective and constraints" in provider.requests[0].messages[0].text()
        assert SUMMARY in provider.requests[-1].messages[0].text()
        assert not any(m.text() == prompt for m in provider.requests[-1].messages)
        assert estimate_request_tokens(provider.requests[-1]) <= 1000
        restarted = TranscriptLog(JsonlTranscriptStore(session.log.session_dir))
        await restarted.load()
        req = await ContextAssembler(rt.registry, rt.settings).assemble(session_id=session.info.session_id,
            history_records=restarted.records, prompt="a new prompt", model="scripted-v1")
        assert SUMMARY in req.messages[0].text()
        assert req.messages[-1].text() == "a new prompt"
        assert not any(m.text() == prompt for m in req.messages)
    finally:
        await rt.registry.unload_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [None, 0.000001])
async def test_summarizer_usage_is_accounted_and_cannot_bypass_turn_spend_cap(tmp_path, cap):
    from harness.sdk.types import Usage, UsageEvent

    provider = ScriptedProvider(responses=[
        [TextDelta(SUMMARY), UsageEvent(Usage(input_tokens=100, output_tokens=50)), Done(stop_reason="end_turn")],
        [TextDelta("answer"), UsageEvent(Usage(input_tokens=20, output_tokens=10)), Done(stop_reason="end_turn")],
    ], strict_exhaustion=True)
    rt, _ = await boot(tmp_path, provider)
    try:
        session = await rt.session_manager.create_session()
        await session.log.append({"role": "user", "content": "history " * 1000})
        result = await rt.runner.execute_turn(session=session.info, prompt="continue",
            options={"provider": "scripted", "model": "scripted-v1", "spend_cap_usd": cap})
        totals = await rt.runner.cost_ledger.totals(session_id=session.info.session_id)
        assert totals.input_tokens == (100 if cap is not None else 120)
        assert totals.output_tokens == (50 if cap is not None else 60)
        if cap is not None:
            assert result.stop_reason == "spend_cap_exceeded"
            assert len(provider.requests) == 1
            assert not [r for r in session.log.records if r["kind"] == "compaction.summary"]
        else:
            assert result.error is None
            assert result.usage.input_tokens == 120 and result.usage.output_tokens == 60
    finally:
        await rt.registry.unload_all()

"""T07: an unacknowledged crash fragment must not consume the next append."""
from __future__ import annotations

import asyncio
import json
import sys
import threading

import pytest

from harness.kernel.transcript import TranscriptLog
from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore


@pytest.mark.parametrize("damaged", ["full_path", "compact_path"])
async def test_unterminated_tail_preserved_without_swallowing_new_record(tmp_path, damaged):
    store = JsonlTranscriptStore(tmp_path)
    await store.append({"role": "user", "content": "before"})
    path = getattr(store, damaged)
    with path.open("ab") as stream:
        stream.write(b'{"partial":')
    original = path.read_bytes()

    restarted = JsonlTranscriptStore(tmp_path)
    acknowledged = await restarted.append({"role": "user", "content": "after-resume"})
    assert acknowledged == 2  # Count valid step indices, not corrupt physical lines.
    for compact in (False, True):
        records = await restarted.read(compact=compact)
        assert [r["content"] for r in records] == ["before", "after-resume"]
    assert path.read_bytes().startswith(original), "Recovery must not rewrite/drop old bytes"
    compact_record = (await restarted.read(compact=True))[-1]
    with restarted.full_path.open("rb") as stream:
        stream.seek(compact_record["full_offset"])
        assert json.loads(stream.readline())["content"] == "after-resume"


@pytest.mark.parametrize("damaged_line", [b"[]\n", b"null\n", b'"text"\n',
    b'{"step_index":"bad","content":"invalid"}\n',
    b'{"step_index":true,"content":"invalid"}\n',
    b'{"step_index":null,"content":"invalid"}\n'])
async def test_nonrecord_json_cannot_erase_recovered_history(tmp_path, damaged_line):
    store = JsonlTranscriptStore(tmp_path)
    await store.append({"role": "user", "content": "before"})
    for path in (store.full_path, store.compact_path):
        with path.open("ab") as stream:
            stream.write(damaged_line)
    restarted = JsonlTranscriptStore(tmp_path)
    assert await restarted.append({"role": "user", "content": "after"}) == 2
    for compact in (False, True):
        assert [r["content"] for r in await restarted.read(compact=compact)] == ["before", "after"]


async def test_valid_unterminated_record_and_highest_persisted_index_are_retained(tmp_path):
    store = JsonlTranscriptStore(tmp_path)
    await store.append({"role": "user", "content": "before"})
    for path in (store.full_path, store.compact_path):
        with path.open("ab") as stream:
            stream.write(json.dumps({"step_index": 40, "content": "valid tail", "role": "user"}).encode())
    restarted = JsonlTranscriptStore(tmp_path)
    assert await restarted.append({"role": "user", "content": "after"}) == 41
    for compact in (False, True):
        assert [r["content"] for r in await restarted.read(compact=compact)] == ["before", "valid tail", "after"]


async def test_cancelled_append_commits_disk_and_log_projection_together(tmp_path, monkeypatch):
    store = JsonlTranscriptStore(tmp_path)
    log = TranscriptLog(store, session_id="test", session_dir=tmp_path)
    await log.append({"role": "user", "content": "before"})
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = asyncio.to_thread

    async def paused_thread(function, *args, **kwargs):
        if function.__name__ != "_write":
            return await original(function, *args, **kwargs)

        def blocked():
            started.set()
            release.wait(timeout=2)
            try:
                return function(*args, **kwargs)
            finally:
                finished.set()

        return await original(blocked)

    monkeypatch.setattr(asyncio, "to_thread", paused_thread)
    task = asyncio.create_task(log.append({"role": "user", "content": "committed-during-cancel"}))
    try:
        assert await original(started.wait, 1), "setup: writer did not reach barrier"
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await original(finished.wait, 1)
        disk = await log.read(compact=False)
        assert [r["content"] for r in disk] == ["before", "committed-during-cancel"]
        assert [r["content"] for r in log.records] == [r["content"] for r in disk]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_full_only_failed_write_reserves_its_index_on_restart(tmp_path, monkeypatch):
    from pathlib import Path

    store = JsonlTranscriptStore(tmp_path)
    await store.append({"content": "before"})
    original = Path.open

    class FailedCompact:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, data):
            raise OSError("synthetic compact write failure")

    def fail_compact(path, mode="r", *args, **kwargs):
        stream = original(path, mode, *args, **kwargs)
        return FailedCompact(stream) if path == store.compact_path and mode == "a+b" else stream

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail_compact)
        with pytest.raises(OSError):
            await store.append({"content": "unacknowledged-full-only"})
    restarted = JsonlTranscriptStore(tmp_path)
    assert await restarted.append({"content": "acknowledged-after-restart"}) == 3
    full = await restarted.read(compact=False)
    compact = await restarted.read(compact=True)
    assert [r["step_index"] for r in full] == [1, 2, 3]
    assert [r["step_index"] for r in compact] == [1, 3]
    assert full[-1]["content"] == compact[-1]["content"] == "acknowledged-after-restart"


async def test_partial_store_failure_invalidates_loaded_projection(tmp_path):
    from harness.kernel.transcript import TranscriptAppendError

    store = JsonlTranscriptStore(tmp_path)
    log = TranscriptLog(store, session_id="test", session_dir=tmp_path)
    await log.load()
    await log.append({"role": "user", "content": "before"})

    class PartialFailure:
        async def append(self, data):
            await store.append(data)
            raise OSError("synthetic failure after persisted full data")

        async def read(self, **kwargs):
            return await store.read(**kwargs)

    log._store = PartialFailure()
    with pytest.raises(TranscriptAppendError):
        await log.append({"role": "user", "content": "persisted-but-unacknowledged"})
    assert not log.loaded, "A failed append must not leave a supposedly current cache"
    recovered = await log.load()
    assert [r["content"] for r in recovered] == ["before", "persisted-but-unacknowledged"]


async def test_direct_store_cancel_does_not_release_lock_before_thread_commit(tmp_path, monkeypatch):
    store = JsonlTranscriptStore(tmp_path)
    started, release = threading.Event(), threading.Event()
    original = asyncio.to_thread

    async def blocked(function, *args, **kwargs):
        if function.__name__ != "_write":
            return await original(function, *args, **kwargs)

        def write():
            started.set()
            release.wait(timeout=2)
            return function(*args, **kwargs)

        return await original(write)

    monkeypatch.setattr(asyncio, "to_thread", blocked)
    task = asyncio.create_task(store.append({"content": "first"}))
    try:
        assert await original(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)  # Let cancellation run; no timing-based wait.
        assert store._lock.locked() and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await store.append({"content": "second"}) == 2
        assert [r["content"] for r in await store.read(compact=False)] == ["first", "second"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("damaged_line", [
    b"[" * 2000 + b'{"unfinished":',
    b'{"step_index":' + b"9" * 5000 + b',"content":"unparseable integer"}\n',
], ids=["deep_truncated_tail", "oversized_integer"])
async def test_decoder_limits_do_not_erase_valid_prefix(tmp_path, damaged_line, monkeypatch):
    if damaged_line.startswith(b"["):
        # Pytest/plugins may raise the recursion limit; exercise the actual
        # decoder boundary without changing the production limit.
        damaged_line = b"[" * (sys.getrecursionlimit() + 100) + b'{"unfinished":'
        # This host's decoder tolerates/rejects the real deep tail as ordinary
        # JSONDecodeError. Inject the documented alternate decoder-limit class
        # for this exact damaged line; all valid prefix/suffix records use json.
        original = json.loads

        def limited(data, *args, **kwargs):
            if data.startswith(b"["):
                raise RecursionError("synthetic decoder nesting limit")
            return original(data, *args, **kwargs)

        monkeypatch.setattr(json, "loads", limited)
    store = JsonlTranscriptStore(tmp_path)
    await store.append({"content": "before"})
    for path in (store.full_path, store.compact_path):
        with path.open("ab") as stream:
            stream.write(damaged_line)
    restarted = JsonlTranscriptStore(tmp_path)
    assert await restarted.append({"content": "after"}) == 2
    for compact in (False, True):
        assert [r["content"] for r in await restarted.read(compact=compact)] == ["before", "after"]


@pytest.mark.parametrize("consumer", ["approval", "gate", "hook", "plugin", "marker", "message"])
async def test_append_receipt_selects_its_record_despite_interleaved_notice(consumer, monkeypatch):
    from types import SimpleNamespace

    from harness.kernel.commands import _PluginCommandRuntime
    from harness.kernel.context import append_and_publish_record
    from harness.kernel.gate import PolicyGate
    from harness.kernel.interactions import InteractionBroker
    from harness.kernel.runner import KernelServicesImpl, TurnRunner
    from harness.sdk.events import SystemNotice, TurnCancelled
    from harness.sdk.types import Message, Role, TextBlock, TranscriptRecord

    log = TranscriptLog(None, session_id="test")
    original = log.append
    kind = {"approval": "approval.requested", "gate": "tool.decision", "hook": "system.notice",
            "plugin": "system.notice", "marker": "turn.cancelled", "message": "user.message"}[consumer]

    async def interleaved(record):
        step = await original(record)
        await original(TranscriptRecord(kind="system.notice", role="system", content="unrelated later fact"))
        return step

    monkeypatch.setattr(log, "append", interleaved)
    if consumer == "approval":
        broker = InteractionBroker(log_for=lambda sid: log)
        recorded = await broker._record("test", kind, "intended fact", {"call_id": "wanted"})
    elif consumer == "gate":
        gate = PolicyGate.__new__(PolicyGate)
        recorded = await gate._record(log, kind, "intended fact", {"call_id": "wanted"})
    elif consumer == "hook":
        recorded = await append_and_publish_record(log=log, bus=None, session_id="test", turn_id=None,
            payload=SystemNotice(message="intended fact"), content="intended fact", metadata={"call_id": "wanted"})
    elif consumer == "plugin":
        holder = SimpleNamespace(_session=lambda sid: SimpleNamespace(log=log), _commands=SimpleNamespace(bus=None))
        recorded = await _PluginCommandRuntime.emit_record(holder, session_id="test", turn_id=None,
            payload=SystemNotice(message="intended fact"), content="intended fact", metadata={"call_id": "wanted"})
    elif consumer == "marker":
        recorded = await TurnRunner._append_marker(TurnRunner.__new__(TurnRunner), SimpleNamespace(log=log), TurnCancelled(reason="intended fact"))
    else:
        indexed = []

        class Index:
            async def index(self, sid, row):
                indexed.append(row)

        holder = SimpleNamespace(log=log, search_index=Index(), session=SimpleNamespace(session_id="test"))
        await KernelServicesImpl.record(holder, Message(role=Role.USER, content=[TextBlock("intended fact")]))
        recorded = indexed[0]
    assert recorded["kind"] == kind
    assert recorded == log.records[0]
    assert recorded["step_index"] == 1

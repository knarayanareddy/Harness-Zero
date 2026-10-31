"""The session transcript log — one append-only record stream per session (WS-04).

Before this, three things made the transcript a *cache* of the conversation rather than its
definition: `KernelServicesImpl` kept a parallel in-memory list, a store append failure was logged as
a warning while the turn continued, and records were untyped `role` + bags. Anything that diverged
from the file was invisible: a session that failed to persist looked identical to one that succeeded,
and a reloaded session showed less than the live one because approvals, decisions and cards had never
been in the log at all.

So the log owns the write path (`append` is fail-closed) and the in-memory list is a read-through
cache of it (`records`), upgraded to v2 on read so every consumer — `derive_messages`, `sessions.get`,
the WS-03 read ledger — sees the same typed records.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from harness.sdk.types import RECORD_KINDS, TranscriptRecord, infer_record_kind

logger = logging.getLogger(__name__)

RECORD_VERSION = 2

#: Kinds that read as a message — to a human or to a model. Everything else in the log (turn markers,
#: decisions, approvals, cards) is a record *about* the conversation, not a message in it, so a session
#: that ran one tool call has three messages and eight records. `MESSAGE_KINDS == MODEL_VISIBLE_KINDS`
#: is asserted in tests/kernel/test_derive_messages.py: the counter and the projection may not drift.
MESSAGE_KINDS = frozenset({"user.message", "assistant.message", "tool.result", "compaction.summary"})

# v1 lines sometimes carried a `source`/`type` instead of a role (the pre-WS-04 store shape and the
# `COMPACTION_SUMMARY` marker). Only used when `role` is missing.
_ROLE_FROM_LEGACY_TYPE = {
    "USER": "user",
    "USER_INPUT": "user",
    "MODEL": "assistant",
    "ASSISTANT": "assistant",
    "PLANNER_RESPONSE": "assistant",
    "TOOL": "user",
    "TOOL_RESULT": "user",
    "SYSTEM": "system",
    "COMPACTION_SUMMARY": "system",
}


class TranscriptAppendError(RuntimeError):
    """The log could not be written, so the step does not happen (WS-04 §1.3).

    Silently continuing is the dangerous option: the model would keep acting on a conversation that
    is not being recorded, and the next reload would show a session that never happened.
    """

    def __init__(self, *, session_id: str = "", reason: str = "", step_index: int | None = None) -> None:
        super().__init__(f"transcript append failed for session {session_id or '<none>'}: {reason}")
        self.session_id = session_id
        self.reason = reason
        self.step_index = step_index


def _legacy_role(data: dict[str, Any]) -> str:
    for key in ("type", "source"):
        value = str(data.get(key, "")).upper()
        if value in _ROLE_FROM_LEGACY_TYPE:
            return _ROLE_FROM_LEGACY_TYPE[value]
    return "user"


def upgrade_record_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Returns ``data`` as a v2 record: additive, lossless, and idempotent.

    Deliberately *not* a round-trip through `TranscriptRecord`: the store stamps extra keys onto a line
    (`call_id`, `tool_name`, `is_error`, `full_offset`, `truncated_fields`) that the dataclass does not
    model, and re-formatting would throw them away — an upgrade would be a downgrade of exactly the
    fields the UI reads.

    Three conventions make "upgrade" invisible, which is what lets one cache serve both a live session and
    a reloaded one:
      * empty bags are not carried (the store `setdefault`s `metadata: {}`, and a decision has no results);
      * a v1 line's single result (`tool_use_id` + `content`) becomes a `tool_results` bag — for untyped
        lines only, so a v2 record that merely mentions a `call_id` is not invented into a tool result;
      * the store's metadata promotions are applied here too, so a record is the same dict in memory and
        on disk whichever of the two wrote it.
    """
    out = dict(data)
    role = str(out.get("role") or "") or _legacy_role(out)
    out["role"] = role

    declared = str(out.get("kind") or "")
    raw_meta = out.get("metadata")
    metadata = raw_meta if isinstance(raw_meta, dict) else {}
    if str(out.get("type", "")).upper() == "COMPACTION_SUMMARY":
        metadata = {**metadata, "compaction_summary": True}

    for key in ("call_id", "tool_name", "is_error"):
        if out.get(key) in (None, "") and key in metadata:
            out[key] = metadata[key]

    raw_results = out.get("tool_results")
    tool_results = raw_results if isinstance(raw_results, list) else []
    if declared not in RECORD_KINDS and not tool_results and (out.get("tool_use_id") or out.get("call_id")):
        tool_results = [
            {
                "tool_use_id": out.get("tool_use_id") or out.get("call_id") or "",
                "content": [{"type": "text", "text": out.get("content", "")}],
                "is_error": bool(out.get("is_error", False)),
            }
        ]

    raw_calls = out.get("tool_calls")
    tool_calls = raw_calls if isinstance(raw_calls, list) else []

    for key, value in (("tool_calls", tool_calls), ("tool_results", tool_results), ("metadata", metadata)):
        current = out.get(key)
        if current is not None and not isinstance(current, (list, dict)):
            continue  # a store placeholder ("[3 items] … [TRUNCATED]") is not a bag to normalise
        if value:
            out[key] = value
        else:
            out.pop(key, None)

    kind = declared if declared in RECORD_KINDS else infer_record_kind(
        role=role, tool_calls=tool_calls, tool_results=tool_results, metadata=metadata
    )
    out["kind"] = kind
    out.setdefault("timestamp", time.time())
    out["v"] = RECORD_VERSION
    return out


def is_v2(data: dict[str, Any]) -> bool:
    return int(data.get("v", 0) or 0) >= RECORD_VERSION and str(data.get("kind") or "") in RECORD_KINDS


#: One log per session directory per process. `SessionManager` keeps its `Session` objects for the
#: lifetime of the process, so this map is bounded the same way — and it has to be shared, because two
#: `TranscriptLog`s (or two JSONL store instances) over one file each keep their own step counter and
#: their own cache, which is exactly how the runner and the session came to disagree about history.
_LOG_BY_DIR: dict[str, TranscriptLog] = {}


def record_at_step(records: list[dict[str, Any]], step: int) -> dict[str, Any]:
    """Resolve an append receipt without assuming no other writer ran afterward."""
    for record in reversed(records):
        if record.get("step_index") == step:
            return record
    raise ValueError(f"Committed transcript step {step!r} is absent from the projection")


class TranscriptLog:
    """Append-only view of one session's records, with a read-through cache.

    `records` is the same list object for the lifetime of the log, so anything that wants the
    history-as-of-now (message building, the WS-03 read ledger) can hold a reference to it instead of
    maintaining a mirror that can drift.
    """

    @classmethod
    def for_session(
        cls, session_dir: Path | str, *, session_id: str = "", store: Any = None
    ) -> TranscriptLog:
        key = str(Path(session_dir).resolve())
        log = _LOG_BY_DIR.get(key)
        if log is None:
            log = cls(store, session_id=session_id, session_dir=session_dir)
            _LOG_BY_DIR[key] = log
        elif store is not None and log._store is None:
            # Whoever got there first may not have had a store plugin to hand; upgrade it, never downgrade.
            log._store = store
        return log

    def __init__(self, store: Any = None, *, session_id: str = "", session_dir: Path | str | None = None) -> None:
        self._store = store
        self.session_id = session_id
        self.session_dir = Path(session_dir) if session_dir else None
        self._cache: list[dict[str, Any]] = []
        self._loaded = False

    # ------------------------------------------------------------------ state
    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def records(self) -> list[dict[str, Any]]:
        """The current history (v2 dicts). Never copy this — it is the shared cache."""
        return self._cache

    def __len__(self) -> int:
        return len(self._cache)

    @property
    def store(self) -> Any:
        return self._store

    # ------------------------------------------------------------------ read
    async def load(self, *, compact: bool = False, force: bool = False) -> list[dict[str, Any]]:
        """Reads the log from the store and caches it (``compact=False``: the model needs full text)."""
        if self._loaded and not force:
            return self._cache
        raw: list[dict[str, Any]] = []
        if self._store is not None:
            try:
                raw = list(await self._store.read(compact=compact) or [])
            except Exception as err:  # noqa: BLE001 — an unreadable log is an empty log, not a crash
                logger.warning("transcript read failed for %s: %s", self.session_id, err)
        # Upgrades are applied to the *cache*, not written back: a log line is immutable once appended,
        # so rewriting it to v2 would make the audit trail lie about what was recorded when.
        self._cache = [upgrade_record_v1(r) for r in raw]
        self._loaded = True
        return self._cache

    async def read(self, *, compact: bool = False, since_step: int = 0) -> list[dict[str, Any]]:
        """Fresh read from the store (used by `sessions.get` and by the reload-equals-live tests)."""
        if self._store is None:
            return [r for r in self._cache if int(r.get("step_index", 0) or 0) >= since_step]
        raw = list(await self._store.read(compact=compact, since_step=since_step) or [])
        return [upgrade_record_v1(r) for r in raw]

    async def read_records(self, *, compact: bool = False) -> list[TranscriptRecord]:
        return [TranscriptRecord.from_dict(r) for r in await self.read(compact=compact)]

    async def last_compaction(self) -> dict[str, Any] | None:
        if self._store is not None and hasattr(self._store, "last_compaction"):
            found = await self._store.last_compaction()
            if found:
                return upgrade_record_v1(found)
        for rec in reversed(self._cache):
            if rec.get("kind") == "compaction.summary" or rec.get("metadata", {}).get("compaction_summary"):
                return rec
        return None

    # ------------------------------------------------------------------ write
    async def append(self, record: TranscriptRecord | dict[str, Any]) -> int:
        """Appends one record and returns its step index. Raises `TranscriptAppendError` on failure."""
        commit = asyncio.create_task(self._append_committed(record))
        try:
            return await asyncio.shield(commit)
        except asyncio.CancelledError:
            # Disk and its canonical projection are one owned commit boundary.
            # Propagate cancellation only after the writer/cache reach agreement.
            while not commit.done():
                try:
                    await asyncio.shield(commit)
                except asyncio.CancelledError:
                    continue
            if not commit.cancelled():
                commit.exception()
            raise

    async def _append_committed(self, record: TranscriptRecord | dict[str, Any]) -> int:
        data = record.to_dict() if isinstance(record, TranscriptRecord) else dict(record)
        data = upgrade_record_v1(data)
        if self._store is None:
            # No store plugin: the log still has to be a log, so the cache is the record of truth for
            # this process and the absence is loud in the debug log rather than silent.
            logger.debug("transcript log has no store; keeping %s in memory only", data.get("kind"))
            step = len(self._cache) + 1
            data["step_index"] = step
            self._cache.append(data)
            return step
        try:
            step = int(await self._store.append(data) or 0)
        except Exception as err:  # noqa: BLE001 — any failure to persist is fatal to the step
            # A failed dual write may still have placed a valid full record on
            # disk. The next turn must reload, not trust the old projection.
            self._loaded = False
            raise TranscriptAppendError(session_id=self.session_id, reason=str(err)) from err
        data["step_index"] = step or len(self._cache) + 1
        self._cache.append(data)
        return data["step_index"]

    def dangling_tool_calls(self) -> list[dict[str, Any]]:
        """`tool_use` blocks in the log that no later record answered.

        The crash shape this looks for: an assistant record was written with tool calls, and the process
        died before the results were. `derive_messages` repairs the *view* (it has to — a provider 400s
        on an unpaired `tool_use`), but the repair belongs in the log too, otherwise every subsequent
        request re-invents it and nothing on disk says the call was never answered.
        """
        answered: set[str] = set()
        for rec in self.records:
            for res in rec.get("tool_results") or []:
                tid = res.get("tool_use_id") if isinstance(res, dict) else None
                if tid:
                    answered.add(str(tid))
            meta = rec.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("call_id") and str(rec.get("kind") or "") == "tool.result":
                answered.add(str(meta["call_id"]))
        out: list[dict[str, Any]] = []
        for rec in self.records:
            for call in rec.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                cid = str(call.get("id") or "")
                if cid and cid not in answered:
                    out.append({"call_id": cid, "name": str(call.get("name") or ""), "record_index": rec.get("step_index")})
                    answered.add(cid)
        return out

    async def repair_dangling_tool_calls(self) -> list[dict[str, Any]]:
        """Appends an explicit error result for every unanswered tool call; returns what was written.

        Synthetic and labelled as such (`metadata.synthetic`): a fabricated success would be the worse
        lie, and the alternative — repairing only in memory — leaves a transcript that cannot explain the
        gap to whoever reads it next.
        """
        from harness.sdk.types import TranscriptRecord

        appended: list[dict[str, Any]] = []
        for dangling in self.dangling_tool_calls():
            name = dangling["name"] or "tool"
            rec = TranscriptRecord(
                kind="tool.result",
                role="tool",
                content=(
                    f"[Synthetic result] The turn ended before `{name}` recorded a result, so this call "
                    "is closed as an error rather than left dangling. Check whether the tool ran."
                ),
                metadata={
                    "call_id": dangling["call_id"],
                    "tool_name": name,
                    "is_error": True,
                    "synthetic": True,
                    "repair": True,
                    "source_record_index": dangling.get("record_index"),
                },
                tool_results=[
                    {
                        "tool_use_id": dangling["call_id"],
                        "content": [{"type": "text", "text": "Tool call was never answered (repaired on resume)."}],
                        "is_error": True,
                    }
                ],
            )
            step = await self.append(rec)
            appended.append(record_at_step(self.records, step))
        return appended

    async def append_notice(self, text: str, *, metadata: dict[str, Any] | None = None) -> int:
        """A user-visible system notice (`model_visible: True` also reaches the model)."""
        return await self.append(
            TranscriptRecord(
                role="system",
                kind="system.notice",
                content=text,
                metadata=dict(metadata or {}),
            )
        )

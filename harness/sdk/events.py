"""
Event definitions, typed payloads, envelopes and JSON Schema for the Harness-Zero EventBus.

Events are the unidirectional, observable state stream of the agent engine. Every event
type has exactly one payload dataclass below; the kernel constructs events from these
classes, ``schema()`` derives the published JSON Schema from them, and ``validate_event``
checks an envelope against them (the test suite does this for every published event).
Frontends must read only the fields declared here.
"""
from __future__ import annotations

import dataclasses
import types as _types
import uuid
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, ClassVar, Literal, Union, get_args, get_origin, get_type_hints

from harness.sdk.types import EconomicsSnapshot, Usage

# ---------------------------------------------------------------------------------------
# Payload base + registry
# ---------------------------------------------------------------------------------------

EVENT_PAYLOADS: dict[str, type[EventPayload]] = {}


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


class EventPayload:
    """Base class for typed event payloads. Subclasses declare ``EVENT_TYPE`` and are dataclasses.

    Two more ClassVars place an event against the session log (WS-04), and `harness catalog gen`
    renders both into `docs/events.schema.json` — they are the machine-readable form of "the log is the
    source of truth":

    ``record_kind``
        The `TranscriptRecord` kind this event mirrors. An event that names a kind is **durable**: the
        same fact exists as a record in the session log, so a client can re-render it after a reload, and
        the publisher can attach the stored record itself on ``Event.record``.
    ``transient``
        Streamed or derived noise (`*.delta`, cost updates, context assembly) that has no record and that
        no replay may depend on. Anything neither durable nor transient (e.g. ``plugin.loaded``) is a
        kernel fact with no session-log counterpart — visible live, not part of any transcript.
    """

    EVENT_TYPE: ClassVar[str] = ""
    record_kind: ClassVar[str | None] = None
    transient: ClassVar[bool] = False
    #: Whether a publisher attaches the stored record to the envelope (`Event.record`). `False` together
    #: with a `record_kind` means: the fact *is* in the log, but someone else writes it or in another
    #: shape, so event and record pair by a stable id instead. `tool.result` is the case that matters — a
    #: strategy batches a step's results into one record (the shape a provider requires) while the gate
    #: publishes one preview event per call, and those pair by `call_id` / `tool_use_id`.
    carries_record: ClassVar[bool] = True

    @classmethod
    def durable(cls) -> bool:
        return cls.record_kind is not None

    def to_dict(self) -> dict[str, Any]:
        return {f.name: _to_jsonable(getattr(self, f.name)) for f in fields(self)}  # type: ignore[arg-type]


def event_payload(cls: type[EventPayload]) -> type[EventPayload]:
    """Registers a payload dataclass under its ``EVENT_TYPE`` (apply outermost, above ``@dataclass``)."""
    if not cls.EVENT_TYPE:
        raise TypeError(f"{cls.__name__} must declare EVENT_TYPE")
    if cls.EVENT_TYPE in EVENT_PAYLOADS:
        raise TypeError(f"duplicate payload for event type {cls.EVENT_TYPE!r}")
    EVENT_PAYLOADS[cls.EVENT_TYPE] = cls
    return cls


# ---------------------------------------------------------------------------------------
# Payloads — one per event type, sorted by lifecycle
# ---------------------------------------------------------------------------------------


@event_payload
@dataclass(slots=True)
class SessionStarted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "session.started"
    session_id: str
    workspace: str
    strategy: str
    provider: str
    model: str


@event_payload
@dataclass(slots=True)
class SessionSwitched(EventPayload):
    EVENT_TYPE: ClassVar[str] = "session.switched"
    session_id: str
    workspace: str


@event_payload
@dataclass(slots=True)
class PluginLoaded(EventPayload):
    EVENT_TYPE: ClassVar[str] = "plugin.loaded"
    name: str
    version: str
    provides: list[str]
    capabilities: list[str]
    trust: str


@event_payload
@dataclass(slots=True)
class PluginError(EventPayload):
    EVENT_TYPE: ClassVar[str] = "plugin.error"
    name: str
    error: str


@event_payload
@dataclass(slots=True)
class TurnStarted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "turn.started"
    record_kind: ClassVar[str | None] = 'user.message'
    prompt_preview: str
    strategy: str
    provider: str
    model: str
    options: dict[str, Any] = field(default_factory=dict)


@event_payload
@dataclass(slots=True)
class TurnCompleted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "turn.completed"
    record_kind: ClassVar[str | None] = 'turn.completed'
    stop_reason: str
    usage: Usage
    duration_ms: float
    tool_calls: int


@event_payload
@dataclass(slots=True)
class TurnCancelled(EventPayload):
    EVENT_TYPE: ClassVar[str] = "turn.cancelled"
    record_kind: ClassVar[str | None] = 'turn.cancelled'
    reason: str


@event_payload
@dataclass(slots=True)
class TurnError(EventPayload):
    EVENT_TYPE: ClassVar[str] = "turn.error"
    record_kind: ClassVar[str | None] = 'turn.error'
    message: str
    kind: str
    retryable: bool = False


@dataclass(slots=True)
class ContextBlockInfo:
    """One assembled context block (nested in ``context.assembled``)."""

    id: str
    stability: str
    source: str
    tokens: int


@event_payload
@dataclass(slots=True)
class ContextAssembled(EventPayload):
    EVENT_TYPE: ClassVar[str] = "context.assembled"
    transient: ClassVar[bool] = True
    blocks: list[ContextBlockInfo]
    total_tokens: int
    history_messages: int


@event_payload
@dataclass(slots=True)
class ModelStarted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "model.started"
    transient: ClassVar[bool] = True
    provider: str
    model: str
    request_tokens_estimate: int


@event_payload
@dataclass(slots=True)
class ModelTextDelta(EventPayload):
    EVENT_TYPE: ClassVar[str] = "model.text.delta"
    transient: ClassVar[bool] = True
    text: str


@event_payload
@dataclass(slots=True)
class ModelThinkingDelta(EventPayload):
    EVENT_TYPE: ClassVar[str] = "model.thinking.delta"
    transient: ClassVar[bool] = True
    text: str


@event_payload
@dataclass(slots=True)
class ModelToolCall(EventPayload):
    EVENT_TYPE: ClassVar[str] = "model.tool_call"
    transient: ClassVar[bool] = True
    call_id: str
    name: str
    arguments: dict[str, Any]


@event_payload
@dataclass(slots=True)
class ModelNotice(EventPayload):
    EVENT_TYPE: ClassVar[str] = "model.notice"
    message: str


@event_payload
@dataclass(slots=True)
class ModelCompleted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "model.completed"
    record_kind: ClassVar[str | None] = 'assistant.message'
    stop_reason: str
    usage: Usage


@event_payload
@dataclass(slots=True)
class ToolCallStarted(EventPayload):
    """A tool call entered the policy gate (``tool.call``)."""

    EVENT_TYPE: ClassVar[str] = "tool.call"
    transient: ClassVar[bool] = True
    call_id: str
    name: str
    arguments: dict[str, Any]
    risk: str
    plugin: str


@event_payload
@dataclass(slots=True)
class ToolDecision(EventPayload):
    """What the policy chain decided, and who it decided it about.

    The keys mirror the `tool.decision` record exactly (`plugin` included): the card subtitle a client
    paints — `fs-tools · write` — is part of the decision, so a session reloaded from the log can render
    the same card without a live `tool.call` event to supply it.
    """

    EVENT_TYPE: ClassVar[str] = "tool.decision"
    record_kind: ClassVar[str | None] = "tool.decision"
    call_id: str
    action: Literal["allow", "deny", "ask"]
    policy: str
    risk: str
    reason: str
    plugin: str = ""  # owning plugin; "" for a decision taken before a tool was resolved
    metadata: dict[str, Any] | None = None  # enforcement availability, when the decision depends on a sandbox

    def to_dict(self) -> dict[str, Any]:
        data = EventPayload.to_dict(self)
        # Preserve the pre-WS-06 shape for ordinary decisions; the optional metadata is only serialized when a
        # policy actually has an enforcement fact to attach.
        if not self.metadata:
            data.pop("metadata", None)
        return data


@event_payload
@dataclass(slots=True)
class ApprovalRequired(EventPayload):
    EVENT_TYPE: ClassVar[str] = "approval.required"
    record_kind: ClassVar[str | None] = "approval.requested"
    approval_id: str
    call_id: str | None  # None when a strategy asks for approval outside a tool call
    kind: str
    summary: str
    details: dict[str, Any]
    timeout_s: float | None
    options: list[str]


@event_payload
@dataclass(slots=True)
class ApprovalResolved(EventPayload):
    EVENT_TYPE: ClassVar[str] = "approval.resolved"
    record_kind: ClassVar[str | None] = "approval.resolved"
    approval_id: str
    approved: bool
    by: str
    reason: str | None = None


@event_payload
@dataclass(slots=True)
class QuestionAsked(EventPayload):
    """A structured question the model is waiting for a human to answer."""

    EVENT_TYPE: ClassVar[str] = "question.asked"
    record_kind: ClassVar[str | None] = "question.asked"
    interaction_id: str
    call_id: str | None
    turn_id: str | None
    questions: list[dict[str, Any]]
    timeout_s: float | None


@event_payload
@dataclass(slots=True)
class QuestionAnswered(EventPayload):
    """The user's answer, cancellation, or timeout for a structured question."""

    EVENT_TYPE: ClassVar[str] = "question.answered"
    record_kind: ClassVar[str | None] = "question.answered"
    interaction_id: str
    call_id: str | None
    turn_id: str | None
    answers: dict[str, Any]
    status: Literal["answered", "cancelled", "timeout"]
    by: str
    reason: str = ""


@event_payload
@dataclass(slots=True)
class ToolOutputDelta(EventPayload):
    EVENT_TYPE: ClassVar[str] = "tool.output.delta"
    transient: ClassVar[bool] = True
    call_id: str
    stream: str
    text: str


@event_payload
@dataclass(slots=True)
class ToolCallResult(EventPayload):
    EVENT_TYPE: ClassVar[str] = "tool.result"
    record_kind: ClassVar[str | None] = "tool.result"
    carries_record: ClassVar[bool] = False
    call_id: str
    name: str
    is_error: bool
    duration_ms: float
    preview: str
    metadata: dict[str, Any] = field(default_factory=dict)


@event_payload
@dataclass(slots=True)
class ToolResultPruned(EventPayload):
    """Live-only (WS-03): a finalize guard shortened what the model reads for this call.

    Not durable — the pruned text is what the transcript records, and re-deriving the original from
    an event replay would be a second source of truth. The UI uses it for a "pruned" badge.
    """

    EVENT_TYPE: ClassVar[str] = "tool.result.pruned"
    transient: ClassVar[bool] = True
    call_id: str
    name: str
    original_chars: int
    shown_chars: int
    spill_path: str = ""


@event_payload
@dataclass(slots=True)
class ToolRepeatDetected(EventPayload):
    """Live-only (WS-03): this turn repeated an identical call whose result had not changed."""

    EVENT_TYPE: ClassVar[str] = "tool.repeat_detected"
    transient: ClassVar[bool] = True
    call_id: str
    name: str
    count: int
    blocked: bool = False


@event_payload
@dataclass(slots=True)
class TaskStarted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "task.started"
    task_id: str
    label: str


@event_payload
@dataclass(slots=True)
class TaskCompleted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "task.completed"
    task_id: str
    label: str
    exit_code: int | None
    tail: str


@event_payload
@dataclass(slots=True)
class JobStarted(EventPayload):
    """A durable background process entered the jobs registry."""

    EVENT_TYPE: ClassVar[str] = "job.started"
    record_kind: ClassVar[str | None] = "job.started"
    job_id: str
    label: str
    command: str
    cwd: str
    pid: int | None
    started_at: float
    sandbox: dict[str, Any] | None = None


@event_payload
@dataclass(slots=True)
class JobOutput(EventPayload):
    """A coalesced live chunk; its durable record points into the bounded spill file."""

    EVENT_TYPE: ClassVar[str] = "job.output"
    record_kind: ClassVar[str | None] = "job.output"
    job_id: str
    offset_start: int
    offset_end: int
    stream: str
    text: str


@event_payload
@dataclass(slots=True)
class JobExited(EventPayload):
    """A durable terminal state for a completed, stopped, failed, or lost job."""

    EVENT_TYPE: ClassVar[str] = "job.exited"
    record_kind: ClassVar[str | None] = "job.exited"
    job_id: str
    label: str
    exit_code: int | None
    reason: str
    duration_ms: float
    tail: str


@event_payload
@dataclass(slots=True)
class CompactionStarted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "compaction.started"
    transient: ClassVar[bool] = True
    estimated_tokens: int


@event_payload
@dataclass(slots=True)
class CompactionCompleted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "compaction.completed"
    record_kind: ClassVar[str | None] = "compaction.summary"
    carries_record: ClassVar[bool] = True
    summary_tokens: int
    duration_ms: float


@event_payload
@dataclass(slots=True)
class CostUpdated(EconomicsSnapshot, EventPayload):
    """``cost.updated`` carries the same ``EconomicsSnapshot`` the ``economics`` command returns."""

    EVENT_TYPE: ClassVar[str] = "cost.updated"
    transient: ClassVar[bool] = True

    @classmethod
    def from_snapshot(cls, snapshot: EconomicsSnapshot) -> CostUpdated:
        return cls(**{f.name: getattr(snapshot, f.name) for f in fields(snapshot)})


@event_payload
@dataclass(slots=True)
class UiCard(EventPayload):
    """A card for the frontends; ``card`` is ``harness.sdk.cards.Card.to_dict()``."""

    EVENT_TYPE: ClassVar[str] = "ui.card"
    record_kind: ClassVar[str | None] = "ui.card"
    card: dict[str, Any]


@event_payload
@dataclass(slots=True)
class HookFired(EventPayload):
    EVENT_TYPE: ClassVar[str] = "hook.fired"
    hook: str
    event: str
    command: str
    outcome: dict[str, Any]
    duration_ms: float


@event_payload
@dataclass(slots=True)
class SettingsChanged(EventPayload):
    EVENT_TYPE: ClassVar[str] = "settings.changed"
    paths: list[str]


@event_payload
@dataclass(slots=True)
class SystemNotice(EventPayload):
    """Durable user-facing information emitted by a hook or plugin."""

    EVENT_TYPE: ClassVar[str] = "system.notice"
    record_kind: ClassVar[str | None] = "system.notice"
    message: str
    level: Literal["info", "success", "warn", "error"] = "info"
    code: str | None = None


@event_payload
@dataclass(slots=True)
class McpServerStatus(EventPayload):
    """Live status of one configured Model Context Protocol server."""

    EVENT_TYPE: ClassVar[str] = "mcp.server.status"
    transient: ClassVar[bool] = True
    server: str
    transport: str
    state: Literal["connecting", "connected", "reconnecting", "failed", "stopped"]
    attempt: int = 0
    error: str | None = None
    tools_count: int = 0
    session_id_present: bool = False


@event_payload
@dataclass(slots=True)
class McpToolsChanged(EventPayload):
    """A durable notice that an MCP server's advertised tools changed."""

    EVENT_TYPE: ClassVar[str] = "mcp.tools.changed"
    record_kind: ClassVar[str | None] = "system.notice"
    server: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@event_payload
@dataclass(slots=True)
class CheckpointCreated(EventPayload):
    """A commit in the private checkpoint repository, never in the user's own git repository."""

    EVENT_TYPE: ClassVar[str] = "checkpoint.created"
    record_kind: ClassVar[str | None] = "checkpoint.created"
    sha: str
    label: str


@event_payload
@dataclass(slots=True)
class WorkspaceChanges(EventPayload):
    """The net tracked workspace delta between the turn-start and turn-end snapshots."""

    EVENT_TYPE: ClassVar[str] = "workspace.changes"
    record_kind: ClassVar[str | None] = "workspace.changes"
    turn_id: str
    base: str | None
    head: str | None
    files: list[dict[str, Any]]
    added: int
    removed: int
    truncated: bool = False
    snapshot_skipped: bool = False


@event_payload
@dataclass(slots=True)
class DeliverablePresented(EventPayload):
    """The file the agent marked as its user-facing result."""

    EVENT_TYPE: ClassVar[str] = "deliverable.presented"
    record_kind: ClassVar[str | None] = "deliverable.presented"
    turn_id: str
    path: str
    caption: str
    kind: Literal["artifact", "file"]
    size: int
    sha256: str


@event_payload
@dataclass(slots=True)
class CheckpointRestored(EventPayload):
    """A user-approved restore of the shadow repository to an earlier commit."""

    EVENT_TYPE: ClassVar[str] = "checkpoint.restored"
    record_kind: ClassVar[str | None] = "checkpoint.restored"
    sha: str
    label: str
    files_touched: int


KNOWN_EVENT_TYPES = frozenset(EVENT_PAYLOADS)


# ---------------------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------------------


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any]
    id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    session_id: str | None = None
    turn_id: str | None = None
    #: The transcript record this event mirrors, exactly as stored (WS-04). Only set for durable event
    #: types (see `EventPayload.record_kind`), and only when the publisher appended the record *before*
    #: publishing — so a client can render history from `record` alone after a reload with no second
    #: fetch. Omitted (not null) on the wire for every other event.
    record: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        payload = self.data
        if isinstance(payload, EventPayload):
            if payload.EVENT_TYPE and payload.EVENT_TYPE != self.type:
                raise ValueError(f"payload {type(payload).__name__} is for {payload.EVENT_TYPE!r}, not {self.type!r}")
            self.data = payload.to_dict()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "id": self.id,
            "ts": self.ts,
            "data": self.data,
        }
        if self.session_id is not None:
            d["session_id"] = self.session_id
        if self.turn_id is not None:
            d["turn_id"] = self.turn_id
        if self.record is not None:
            d["record"] = _to_jsonable(self.record)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        return cls(
            type=d.get("type", ""),
            id=d.get("id") or f"evt_{uuid.uuid4().hex[:12]}",
            ts=d.get("ts") or datetime.now(UTC).isoformat(),
            data=d.get("data", {}),
            session_id=d.get("session_id"),
            turn_id=d.get("turn_id"),
            record=d.get("record"),
        )


# ---------------------------------------------------------------------------------------
# JSON Schema generation (deterministic) and dependency-free validation
# ---------------------------------------------------------------------------------------

_NONE = type(None)


def _type_schema(tp: Any, defs: dict[str, Any]) -> dict[str, Any]:
    origin = get_origin(tp)
    args = get_args(tp)
    if tp is Any:
        return {}
    if origin is Union or origin is _types.UnionType:
        non_none = [a for a in args if a is not _NONE]
        inner = _type_schema(non_none[0], defs) if len(non_none) == 1 else {"anyOf": [_type_schema(a, defs) for a in non_none]}
        if _NONE in args:
            if isinstance(inner.get("type"), str):
                inner = dict(inner, type=[inner["type"], "null"])
            else:
                inner = {"anyOf": [inner, {"type": "null"}]}
        return inner
    if origin is Literal:
        return {"type": "string", "enum": list(args)}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is str:
        return {"type": "string"}
    if tp is list or origin in (list, tuple):
        return {"type": "array", "items": _type_schema(args[0], defs) if args else {}}
    if tp is dict or origin is dict:
        return {"type": "object"}
    if isinstance(tp, type) and is_dataclass(tp):
        name = tp.__name__
        if name not in defs:
            defs[name] = {}  # placeholder against recursion
            defs[name] = _dataclass_schema(tp, defs)
        return {"$ref": f"#/$defs/{name}"}
    return {}


def _dataclass_schema(cls: type, defs: dict[str, Any]) -> dict[str, Any]:
    hints = get_type_hints(cls)
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in fields(cls):
        props[f.name] = _type_schema(hints[f.name], defs)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            required.append(f.name)
    out: dict[str, Any] = {"title": cls.__name__, "type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    doc = (cls.__doc__ or "").strip()
    if doc and not doc.startswith(cls.__name__ + "("):
        out["description"] = doc.splitlines()[0]
    return out


def schema() -> dict[str, Any]:
    """Generates the deterministic JSON Schema for event envelopes, including one payload schema per type."""
    defs: dict[str, Any] = {}
    payloads = {t: _dataclass_schema(EVENT_PAYLOADS[t], defs) for t in sorted(EVENT_PAYLOADS)}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "HarnessZeroEventEnvelope",
        "type": "object",
        "required": ["type", "id", "ts", "data"],
        "properties": {
            "type": {
                "type": "string",
                "enum": sorted(KNOWN_EVENT_TYPES),
                "description": "Event topic identifier",
            },
            "id": {"type": "string", "description": "Unique event identifier"},
            "ts": {"type": "string", "format": "date-time", "description": "ISO-8601 UTC timestamp of event creation"},
            "session_id": {"type": ["string", "null"], "description": "Session identifier if correlated"},
            "turn_id": {"type": ["string", "null"], "description": "Turn identifier if correlated"},
            "data": {"type": "object", "description": "Event-specific payload; see $defs/payloads/<type>"},
            "record": {
                "type": ["object", "null"],
                "description": "Transcript record this event mirrors, as stored (durable types only)",
            },
        },
        "events": {
            t: {
                "payload": EVENT_PAYLOADS[t].__name__,
                "durable": EVENT_PAYLOADS[t].record_kind is not None,
                "record_kind": EVENT_PAYLOADS[t].record_kind,
                "carries_record": bool(EVENT_PAYLOADS[t].carries_record),
                "transient": bool(EVENT_PAYLOADS[t].transient),
            }
            for t in sorted(EVENT_PAYLOADS)
        },
        "allOf": [
            {
                "if": {"properties": {"type": {"const": t}}},
                "then": {"properties": {"data": {"$ref": f"#/$defs/payloads/{t}"}}},
            }
            for t in sorted(payloads)
        ],
        "$defs": {**{k: defs[k] for k in sorted(defs)}, "payloads": payloads},
    }


def _check(value: Any, tp: Any, path: str, errors: list[str]) -> None:
    origin = get_origin(tp)
    args = get_args(tp)
    if tp is Any:
        return
    if origin is Union or origin is _types.UnionType:
        if value is None and _NONE in args:
            return
        non_none = [a for a in args if a is not _NONE]
        if len(non_none) == 1:
            _check(value, non_none[0], path, errors)
            return
        trial: list[str] = []
        for a in non_none:
            sub: list[str] = []
            _check(value, a, path, sub)
            if not sub:
                return
            trial.extend(sub)
        errors.append(f"{path}: {value!r} matches none of {non_none}")
        return
    if origin is Literal:
        if value not in args:
            errors.append(f"{path}: {value!r} not in {list(args)}")
        return
    if tp is bool:
        if not isinstance(value, bool):
            errors.append(f"{path}: expected bool, got {type(value).__name__}")
        return
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: expected int, got {type(value).__name__}")
        return
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: expected number, got {type(value).__name__}")
        return
    if tp is str:
        if not isinstance(value, str):
            errors.append(f"{path}: expected str, got {type(value).__name__}")
        return
    if tp is list or origin in (list, tuple):
        if not isinstance(value, list):
            errors.append(f"{path}: expected list, got {type(value).__name__}")
            return
        if args:
            for i, item in enumerate(value):
                _check(item, args[0], f"{path}[{i}]", errors)
        return
    if tp is dict or origin is dict:
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object, got {type(value).__name__}")
        return
    if isinstance(tp, type) and is_dataclass(tp):
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object, got {type(value).__name__}")
            return
        _check_dataclass(value, tp, path, errors)


@lru_cache(maxsize=256)
def _validation_metadata(cls: type) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cache static declarations only; no event data or validation result is retained."""
    return get_type_hints(cls), {f.name: f for f in fields(cls)}


def _check_dataclass(data: dict[str, Any], cls: type, path: str, errors: list[str]) -> None:
    hints, declared = _validation_metadata(cls)
    for name, f in declared.items():
        if name not in data:
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                errors.append(f"{path}: missing required field {name!r}")
            continue
        _check(data[name], hints[name], f"{path}.{name}", errors)
    for extra in sorted(set(data) - set(declared)):
        errors.append(f"{path}: unexpected field {extra!r}")


def validate_event(event: Event | dict[str, Any]) -> list[str]:
    """Returns a list of contract violations for an event envelope (empty when it conforms).

    Unknown event types are reported; known types are checked field by field against their
    payload dataclass (required fields, leaf types, no undeclared fields).
    """
    d = event.to_dict() if isinstance(event, Event) else event
    etype = d.get("type", "")
    cls = EVENT_PAYLOADS.get(etype)
    if cls is None:
        return [f"{etype!r} is not a known event type"]
    data = d.get("data")
    if not isinstance(data, dict):
        return [f"{etype}: data must be an object, got {type(data).__name__}"]
    errors: list[str] = []
    _check_dataclass(data, cls, etype, errors)
    if etype == "ui.card" and not errors:
        from harness.sdk.cards import Card  # local import: cards is a leaf module

        try:
            Card.from_dict(data["card"])
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append(f"ui.card.card: {exc}")
    return errors

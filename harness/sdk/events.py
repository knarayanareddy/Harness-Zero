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
    call_id: str
    name: str
    arguments: dict[str, Any]
    risk: str
    plugin: str


@event_payload
@dataclass(slots=True)
class ToolDecision(EventPayload):
    EVENT_TYPE: ClassVar[str] = "tool.decision"
    call_id: str
    action: Literal["allow", "deny", "ask"]
    policy: str
    risk: str
    reason: str


@event_payload
@dataclass(slots=True)
class ApprovalRequired(EventPayload):
    EVENT_TYPE: ClassVar[str] = "approval.required"
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
    approval_id: str
    approved: bool
    by: str
    reason: str | None = None


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
class CompactionStarted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "compaction.started"
    transient: ClassVar[bool] = True
    estimated_tokens: int


@event_payload
@dataclass(slots=True)
class CompactionCompleted(EventPayload):
    EVENT_TYPE: ClassVar[str] = "compaction.completed"
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


def _check_dataclass(data: dict[str, Any], cls: type, path: str, errors: list[str]) -> None:
    hints = get_type_hints(cls)
    declared = {f.name: f for f in fields(cls)}
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

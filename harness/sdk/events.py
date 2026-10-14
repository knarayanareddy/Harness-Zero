"""
Event Definitions, Envelopes, and JSON Schema for Harness-Zero EventBus.
Events represent the unidirectional, observable state stream of the agent engine.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

KNOWN_EVENT_TYPES = frozenset({
    "session.started",
    "session.switched",
    "plugin.loaded",
    "plugin.error",
    "turn.started",
    "turn.completed",
    "turn.cancelled",
    "turn.error",
    "context.assembled",
    "model.started",
    "model.text.delta",
    "model.thinking.delta",
    "model.tool_call",
    "model.notice",
    "model.completed",
    "tool.call",
    "tool.decision",
    "approval.required",
    "approval.resolved",
    "tool.output.delta",
    "tool.result",
    "task.started",
    "task.completed",
    "compaction.started",
    "compaction.completed",
    "cost.updated",
    "ui.card",
    "hook.fired",
    "settings.changed",
    "log",
})


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any]
    id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    session_id: str | None = None
    turn_id: str | None = None

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
        )


def schema() -> dict[str, Any]:
    """Generates deterministic JSON Schema for Harness-Zero event envelopes."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "HarnessZeroEventEnvelope",
        "type": "object",
        "required": ["type", "id", "ts", "data"],
        "properties": {
            "type": {
                "type": "string",
                "enum": sorted(list(KNOWN_EVENT_TYPES)),
                "description": "Event topic identifier",
            },
            "id": {
                "type": "string",
                "description": "Unique event identifier",
            },
            "ts": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 UTC timestamp of event creation",
            },
            "session_id": {
                "type": ["string", "null"],
                "description": "Session identifier if correlated",
            },
            "turn_id": {
                "type": ["string", "null"],
                "description": "Turn identifier if correlated",
            },
            "data": {
                "type": "object",
                "description": "Event-specific payload",
            },
        },
    }

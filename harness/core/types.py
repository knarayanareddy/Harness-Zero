"""
Core data types and schemas for Harness-Zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class StepType(str, Enum):
    USER_INPUT = "USER_INPUT"
    PLANNER_RESPONSE = "PLANNER_RESPONSE"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    SYSTEM_NOTIFICATION = "SYSTEM_NOTIFICATION"
    REACTIVE_WAKEUP = "REACTIVE_WAKEUP"
    COMPACTION_SUMMARY = "COMPACTION_SUMMARY"


class StepSource(str, Enum):
    USER = "USER"
    MODEL = "MODEL"
    TOOL = "TOOL"
    SYSTEM = "SYSTEM"


class ModelTier(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    REASONING = "reasoning"


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class ToolResult:
    call_id: str
    name: str
    output: str
    is_error: bool = False
    task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "call_id": self.call_id,
            "name": self.name,
            "output": self.output,
            "is_error": self.is_error,
        }
        if self.task_id:
            data["task_id"] = self.task_id
        if self.metadata:
            data["metadata"] = self.metadata
        return data


@dataclass
class MediaRef:
    mime_type: str
    uri: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mime_type": self.mime_type,
            "uri": self.uri,
        }


@dataclass
class TranscriptRecord:
    step_index: int
    source: str
    type: str
    content: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    media: list[dict[str, Any]] = field(default_factory=list)
    status: str = "DONE"
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    truncated_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "step_index": self.step_index,
            "source": self.source,
            "type": self.type,
            "status": self.status,
            "created_at": self.created_at,
            "content": self.content,
        }
        if self.thinking:
            data["thinking"] = self.thinking
        if self.tool_calls:
            data["tool_calls"] = self.tool_calls
        if self.media:
            data["media"] = self.media
        if self.truncated_fields:
            data["truncated_fields"] = self.truncated_fields
        return data


@dataclass
class ModelResponse:
    content: str
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_response: dict[str, Any] | None = None
    finish_reason: str = "stop"

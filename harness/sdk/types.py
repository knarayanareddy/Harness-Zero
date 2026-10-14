"""
Core Domain Types for Harness-Zero SDK.
All types are slot dataclasses or string enums, fully serializable to and from dict.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

SDK_API_VERSION = "1"


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(slots=True, frozen=True)
class TextBlock:
    text: str
    type: Literal["text"] = "text"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextBlock:
        return cls(text=data.get("text", ""))


@dataclass(slots=True, frozen=True)
class ImageBlock:
    media_type: str
    data_b64: str | None = None
    url: str | None = None
    type: Literal["image"] = "image"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "media_type": self.media_type}
        if self.data_b64 is not None:
            d["data_b64"] = self.data_b64
        if self.url is not None:
            d["url"] = self.url
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageBlock:
        return cls(
            media_type=data.get("media_type", "image/png"),
            data_b64=data.get("data_b64"),
            url=data.get("url"),
        )


@dataclass(slots=True, frozen=True)
class ThinkingBlock:
    text: str
    signature: str | None = None
    redacted: bool = False
    type: Literal["thinking"] = "thinking"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "text": self.text, "redacted": self.redacted}
        if self.signature is not None:
            d["signature"] = self.signature
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThinkingBlock:
        return cls(
            text=data.get("text", ""),
            signature=data.get("signature"),
            redacted=bool(data.get("redacted", False)),
        )


@dataclass(slots=True, frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, "name": self.name, "input": self.input}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolUseBlock:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            input=data.get("input", {}),
        )


@dataclass(slots=True, frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: list[TextBlock | ImageBlock]
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "tool_use_id": self.tool_use_id,
            "content": [c.to_dict() for c in self.content],
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolResultBlock:
        parsed_content: list[TextBlock | ImageBlock] = []
        for c in data.get("content", []):
            if isinstance(c, dict):
                if c.get("type") == "image":
                    parsed_content.append(ImageBlock.from_dict(c))
                else:
                    parsed_content.append(TextBlock.from_dict(c))
            elif isinstance(c, str):
                parsed_content.append(TextBlock(text=c))
        return cls(
            tool_use_id=data.get("tool_use_id", ""),
            content=parsed_content,
            is_error=bool(data.get("is_error", False)),
        )


ContentBlock = TextBlock | ImageBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


def content_block_from_dict(data: dict[str, Any]) -> ContentBlock:
    block_type = data.get("type", "text")
    if block_type == "text":
        return TextBlock.from_dict(data)
    elif block_type == "image":
        return ImageBlock.from_dict(data)
    elif block_type == "thinking":
        return ThinkingBlock.from_dict(data)
    elif block_type == "tool_use":
        return ToolUseBlock.from_dict(data)
    elif block_type == "tool_result":
        return ToolResultBlock.from_dict(data)
    return TextBlock(text=str(data))


@dataclass(slots=True)
class Message:
    role: Role
    content: list[ContentBlock]
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        parts: list[str] = []
        for b in self.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
        return "".join(parts)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "role": self.role.value if isinstance(self.role, Role) else self.role,
            "content": [b.to_dict() for b in self.content],
        }
        if self.name is not None:
            d["name"] = self.name
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        raw_role = data.get("role", "user")
        role = Role(raw_role) if raw_role in [r.value for r in Role] else Role.USER
        raw_content = data.get("content", [])
        content: list[ContentBlock] = []
        if isinstance(raw_content, str):
            content.append(TextBlock(text=raw_content))
        elif isinstance(raw_content, list):
            for item in raw_content:
                if isinstance(item, dict):
                    content.append(content_block_from_dict(item))
                elif isinstance(item, str):
                    content.append(TextBlock(text=item))
        return cls(
            role=role,
            content=content,
            name=data.get("name"),
            metadata=data.get("metadata", {}),
        )


class RiskClass(str, Enum):
    READ = "read"
    WRITE = "write"
    EXEC = "exec"
    NETWORK = "network"
    DESTRUCTIVE = "destructive"


RISK_ORDER = [
    RiskClass.READ,
    RiskClass.WRITE,
    RiskClass.EXEC,
    RiskClass.NETWORK,
    RiskClass.DESTRUCTIVE,
]


@dataclass(slots=True, frozen=True)
class ToolAnnotations:
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_only": self.read_only,
            "destructive": self.destructive,
            "idempotent": self.idempotent,
            "open_world": self.open_world,
            "title": self.title,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolAnnotations:
        return cls(
            read_only=bool(data.get("read_only", False)),
            destructive=bool(data.get("destructive", False)),
            idempotent=bool(data.get("idempotent", False)),
            open_world=bool(data.get("open_world", False)),
            title=data.get("title"),
        )


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_class: RiskClass = RiskClass.READ
    capabilities: frozenset[str] = field(default_factory=frozenset)
    annotations: ToolAnnotations = field(default_factory=ToolAnnotations)
    namespace: str | None = None
    plugin: str = ""
    timeout_s: float = 600.0
    streams_output: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "risk_class": self.risk_class.value,
            "capabilities": list(self.capabilities),
            "annotations": self.annotations.to_dict(),
            "namespace": self.namespace,
            "plugin": self.plugin,
            "timeout_s": self.timeout_s,
            "streams_output": self.streams_output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolSpec:
        risk_str = data.get("risk_class", "read")
        risk = RiskClass(risk_str) if risk_str in [r.value for r in RiskClass] else RiskClass.READ
        raw_caps = data.get("capabilities", [])
        caps = frozenset(raw_caps) if isinstance(raw_caps, (list, set, frozenset)) else frozenset()
        raw_ann = data.get("annotations", {})
        ann = ToolAnnotations.from_dict(raw_ann) if isinstance(raw_ann, dict) else ToolAnnotations()
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            input_schema=data.get("input_schema", {}),
            risk_class=risk,
            capabilities=caps,
            annotations=ann,
            namespace=data.get("namespace"),
            plugin=data.get("plugin", ""),
            timeout_s=float(data.get("timeout_s", 600.0)),
            streams_output=bool(data.get("streams_output", False)),
        )


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            arguments=data.get("arguments", {}),
        )


@dataclass(slots=True)
class ToolResult:
    content: list[TextBlock | ImageBlock]
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    cards: list[Any] = field(default_factory=list)

    @property
    def output(self) -> str:
        parts: list[str] = []
        for c in self.content:
            if isinstance(c, TextBlock):
                parts.append(c.text)
        return "".join(parts)

    @classmethod
    def text(cls, s: str, is_error: bool = False, **metadata: Any) -> ToolResult:
        return cls(
            content=[TextBlock(text=s)],
            is_error=is_error,
            metadata=metadata,
        )

    @classmethod
    def success(cls, s: str, **metadata: Any) -> ToolResult:
        return cls(
            content=[TextBlock(text=s)],
            is_error=False,
            metadata=metadata,
        )

    @classmethod
    def error(cls, s: str, **metadata: Any) -> ToolResult:
        return cls(
            content=[TextBlock(text=s)],
            is_error=True,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": [c.to_dict() for c in self.content],
            "is_error": self.is_error,
            "metadata": self.metadata,
            "cards": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.cards],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolResult:
        parsed_content: list[TextBlock | ImageBlock] = []
        for c in data.get("content", []):
            if isinstance(c, dict):
                if c.get("type") == "image":
                    parsed_content.append(ImageBlock.from_dict(c))
                else:
                    parsed_content.append(TextBlock.from_dict(c))
            elif isinstance(c, str):
                parsed_content.append(TextBlock(text=c))
        return cls(
            content=parsed_content,
            is_error=bool(data.get("is_error", False)),
            metadata=data.get("metadata", {}),
            cards=data.get("cards", []),
        )


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None

    def __add__(self, other: Usage) -> Usage:
        cost = None
        if self.cost_usd is not None and other.cost_usd is not None:
            cost = self.cost_usd + other.cost_usd
        elif self.cost_usd is not None:
            cost = self.cost_usd
        elif other.cost_usd is not None:
            cost = other.cost_usd

        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cost_usd=cost,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": self.cost_usd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            cache_read_tokens=int(data.get("cache_read_tokens", 0)),
            cache_write_tokens=int(data.get("cache_write_tokens", 0)),
            reasoning_tokens=int(data.get("reasoning_tokens", 0)),
            cost_usd=data.get("cost_usd"),
        )


class Stability(str, Enum):
    STATIC = "static"
    SESSION = "session"
    TURN = "turn"


@dataclass(slots=True)
class ContextBlock:
    id: str
    text: str
    stability: Stability
    priority: int = 100
    source: str = ""
    token_estimate: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "stability": self.stability.value,
            "priority": self.priority,
            "source": self.source,
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextBlock:
        stab_str = data.get("stability", "static")
        stab = Stability(stab_str) if stab_str in [s.value for s in Stability] else Stability.STATIC
        return cls(
            id=data.get("id", ""),
            text=data.get("text", ""),
            stability=stab,
            priority=int(data.get("priority", 100)),
            source=data.get("source", ""),
            token_estimate=int(data.get("token_estimate", 0)),
        )


@dataclass(slots=True)
class ModelParams:
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    stop: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort,
            "stop": self.stop,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelParams:
        return cls(
            temperature=data.get("temperature"),
            max_tokens=data.get("max_tokens"),
            reasoning_effort=data.get("reasoning_effort"),
            stop=data.get("stop", []),
            extra=data.get("extra", {}),
        )


@dataclass(slots=True)
class ModelRequest:
    model: str
    system: list[ContextBlock]
    messages: list[Message]
    tools: list[ToolSpec]
    params: ModelParams
    stream: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "system": [b.to_dict() for b in self.system],
            "messages": [m.to_dict() for m in self.messages],
            "tools": [t.to_dict() for t in self.tools],
            "params": self.params.to_dict(),
            "stream": self.stream,
            "metadata": self.metadata,
        }


# Model Stream Events
@dataclass(slots=True, frozen=True)
class TextDelta:
    text: str


@dataclass(slots=True, frozen=True)
class ThinkingDelta:
    text: str


@dataclass(slots=True, frozen=True)
class ToolCallStart:
    id: str
    name: str


@dataclass(slots=True, frozen=True)
class ToolCallDelta:
    id: str
    arguments_json: str


@dataclass(slots=True, frozen=True)
class ToolCallEnd:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True, frozen=True)
class UsageEvent:
    usage: Usage


@dataclass(slots=True, frozen=True)
class ProviderNotice:
    message: str


@dataclass(slots=True, frozen=True)
class Done:
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "stop_sequence", "cancelled"]


ModelEvent = (
    TextDelta
    | ThinkingDelta
    | ToolCallStart
    | ToolCallDelta
    | ToolCallEnd
    | UsageEvent
    | ProviderNotice
    | Done
)


@dataclass(slots=True, frozen=True)
class ProviderCaps:
    native_tools: bool
    streaming: bool
    vision: bool
    cache_hints: bool
    thinking: bool
    json_output: bool = False
    max_context_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderCaps:
        return cls(
            native_tools=bool(data.get("native_tools", False)),
            streaming=bool(data.get("streaming", False)),
            vision=bool(data.get("vision", False)),
            cache_hints=bool(data.get("cache_hints", False)),
            thinking=bool(data.get("thinking", False)),
            json_output=bool(data.get("json_output", False)),
            max_context_tokens=data.get("max_context_tokens"),
        )


@dataclass(slots=True)
class ModelInfo:
    id: str
    display_name: str
    context_tokens: int | None = None
    input_usd_per_mtok: float | None = None
    output_usd_per_mtok: float | None = None
    caps: ProviderCaps | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "context_tokens": self.context_tokens,
            "input_usd_per_mtok": self.input_usd_per_mtok,
            "output_usd_per_mtok": self.output_usd_per_mtok,
            "caps": self.caps.to_dict() if self.caps else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelInfo:
        caps_data = data.get("caps")
        caps = ProviderCaps.from_dict(caps_data) if isinstance(caps_data, dict) else None
        return cls(
            id=data.get("id", ""),
            display_name=data.get("display_name", ""),
            context_tokens=data.get("context_tokens"),
            input_usd_per_mtok=data.get("input_usd_per_mtok"),
            output_usd_per_mtok=data.get("output_usd_per_mtok"),
            caps=caps,
        )


class Action(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(slots=True)
class Decision:
    action: Action
    reason: str = ""
    risk: RiskClass | None = None
    arguments: dict[str, Any] | None = None
    policy: str = ""

    @staticmethod
    def allow(reason: str = "") -> Decision:
        return Decision(action=Action.ALLOW, reason=reason)

    @staticmethod
    def ask(reason: str = "", risk: RiskClass | None = None) -> Decision:
        return Decision(action=Action.ASK, reason=reason, risk=risk)

    @staticmethod
    def deny(reason: str = "", risk: RiskClass | None = None) -> Decision:
        return Decision(action=Action.DENY, reason=reason, risk=risk)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "risk": self.risk.value if self.risk else None,
            "arguments": self.arguments,
            "policy": self.policy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decision:
        act_str = data.get("action", "allow")
        action = Action(act_str) if act_str in [a.value for a in Action] else Action.ALLOW
        risk_str = data.get("risk")
        risk = RiskClass(risk_str) if risk_str in [r.value for r in RiskClass] else None
        return cls(
            action=action,
            reason=data.get("reason", ""),
            risk=risk,
            arguments=data.get("arguments"),
            policy=data.get("policy", ""),
        )


@dataclass(slots=True)
class ExecRequest:
    command: str | list[str]
    cwd: Path
    env_overlay: dict[str, str] = field(default_factory=dict)
    timeout_s: float | None = 600.0
    interactive: bool = False
    shell: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "cwd": str(self.cwd),
            "env_overlay": self.env_overlay,
            "timeout_s": self.timeout_s,
            "interactive": self.interactive,
            "shell": self.shell,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecRequest:
        return cls(
            command=data.get("command", ""),
            cwd=Path(data.get("cwd", ".")),
            env_overlay=data.get("env_overlay", {}),
            timeout_s=data.get("timeout_s"),
            interactive=bool(data.get("interactive", False)),
            shell=bool(data.get("shell", True)),
        )


@dataclass(slots=True)
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    truncated: bool
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecResult:
        return cls(
            exit_code=data.get("exit_code"),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            truncated=bool(data.get("truncated", False)),
            duration_ms=int(data.get("duration_ms", 0)),
        )


class HookEvent(str, Enum):
    SESSION_START = "session_start"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_COMPACT = "pre_compact"
    STOP = "stop"
    TURN_ERROR = "turn_error"
    NOTIFICATION = "notification"


@dataclass(slots=True)
class HookOutcome:
    action: Literal["continue", "block", "mutate"] = "continue"
    reason: str = ""
    arguments: dict[str, Any] | None = None
    additional_context: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "arguments": self.arguments,
            "additional_context": self.additional_context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HookOutcome:
        return cls(
            action=data.get("action", "continue"),
            reason=data.get("reason", ""),
            arguments=data.get("arguments"),
            additional_context=data.get("additional_context"),
        )


class Capability(str, Enum):
    FS_READ = "fs:read"
    FS_WRITE = "fs:write"
    EXEC = "exec"
    NET = "net"
    SECRETS = "secrets"
    APPROVAL = "approval"
    APPROVAL_BYPASS = "approval:bypass"
    UI = "ui"


@dataclass(slots=True)
class TurnResult:
    output: str
    thinking: str = ""
    usage: Usage = field(default_factory=Usage)
    tool_calls: int = 0
    stop_reason: str = "end_turn"
    error: str | None = None
    cards: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "thinking": self.thinking,
            "usage": self.usage.to_dict(),
            "tool_calls": self.tool_calls,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "cards": [c.to_dict() if hasattr(c, "to_dict") else c for c in self.cards],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnResult:
        usage_data = data.get("usage", {})
        usage = Usage.from_dict(usage_data) if isinstance(usage_data, dict) else Usage()
        return cls(
            output=data.get("output", ""),
            thinking=data.get("thinking", ""),
            usage=usage,
            tool_calls=int(data.get("tool_calls", 0)),
            stop_reason=data.get("stop_reason", "end_turn"),
            error=data.get("error"),
            cards=data.get("cards", []),
        )


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    workspace: Path
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionInfo:
        return cls(
            session_id=data.get("session_id", ""),
            workspace=Path(data.get("workspace", ".")),
            created_at=float(data.get("created_at", 0.0)),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class TaskInfo:
    id: str
    label: str
    running: bool
    exit_code: int | None = None
    tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "running": self.running,
            "exit_code": self.exit_code,
            "tail": self.tail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskInfo:
        return cls(
            id=data.get("id", ""),
            label=data.get("label", ""),
            running=bool(data.get("running", False)),
            exit_code=data.get("exit_code"),
            tail=data.get("tail", ""),
        )


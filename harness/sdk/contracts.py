"""
Structural Contracts for Harness-Zero SDK.
All contracts are runtime_checkable Protocols representing modular plugin components.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, runtime_checkable

from harness.sdk.manifest import PluginManifest
from harness.sdk.types import (
    ContextBlock,
    Decision,
    ExecRequest,
    ExecResult,
    HookEvent,
    HookOutcome,
    ModelEvent,
    ModelInfo,
    ModelRequest,
    ProviderCaps,
    ToolCall,
    ToolResult,
    ToolSpec,
    TurnResult,
    Usage,
)


@runtime_checkable
class Plugin(Protocol):
    manifest: PluginManifest

    async def setup(self, ctx: Any) -> None:
        """Called on plugin initialization with PluginContext."""
        ...

    async def teardown(self) -> None:
        """Called on plugin shutdown for resource cleanup."""
        ...


@runtime_checkable
class Provider(Protocol):
    name: str
    caps: ProviderCaps

    async def list_models(self) -> list[ModelInfo]:
        """Returns available models supported by this provider."""
        ...

    def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Streams model events for the given request. Raises ProviderError on failure."""
        ...

    async def aclose(self) -> None:
        """Closes any underlying network clients or connections."""
        ...


@runtime_checkable
class ToolHandler(Protocol):
    async def __call__(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        """Executes tool with validated arguments and ToolContext."""
        ...


@runtime_checkable
class Policy(Protocol):
    name: str
    order: int  # 0..1000; lower runs first (jail=10, classifier=50, tirith=60, modes=900)

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: Any) -> Decision:
        """Evaluates tool call and returns an Action (ALLOW, ASK, DENY). Must not raise."""
        ...


@runtime_checkable
class Process(Protocol):
    id: str
    pid: int | None

    def output(self) -> AsyncIterator[tuple[Literal["stdout", "stderr"], str]]:
        """Streams stdout and stderr chunks."""
        ...

    async def send_input(self, text: str) -> None:
        """Sends stdin input to process."""
        ...

    async def kill(self, grace_s: float = 2.0) -> None:
        """Terminates or kills process."""
        ...

    async def wait(self) -> ExecResult:
        """Waits for process completion and returns ExecResult."""
        ...


@runtime_checkable
class Executor(Protocol):
    name: str  # "local", "worktree", "docker"
    isolation: Literal["none", "worktree", "container", "vm"]

    async def start(self, req: ExecRequest) -> Process:
        """Launches process and returns active Process handle."""
        ...


@runtime_checkable
class ContextSource(Protocol):
    name: str

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        """Returns context blocks for prompt assembly."""
        ...


@runtime_checkable
class TranscriptStore(Protocol):
    async def append(self, record: dict[str, Any]) -> int:
        """Appends step record and returns step_index."""
        ...

    async def read(self, since_step: int = 0, compact: bool = True) -> list[dict[str, Any]]:
        """Reads transcript records."""
        ...

    async def last_compaction(self) -> dict[str, Any] | None:
        """Returns last compaction record if any."""
        ...

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Lists historical sessions metadata."""
        ...


@runtime_checkable
class SearchIndex(Protocol):
    async def index(self, session_id: str, record: dict[str, Any]) -> None:
        """Indexes transcript record."""
        ...

    async def search(
        self,
        query: str,
        *,
        scope: Literal["session", "workspace"] = "workspace",
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Searches index matching query."""
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    async def write(
        self,
        name: str,
        content: bytes | str,
        *,
        media_type: str = "text/plain",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Writes artifact and returns metadata."""
        ...

    async def read(self, name: str) -> tuple[bytes, dict[str, Any]]:
        """Reads artifact content and metadata."""
        ...

    async def list(self) -> list[dict[str, Any]]:
        """Lists available artifacts."""
        ...


@runtime_checkable
class CostLedger(Protocol):
    async def record(
        self, *, model: str, provider: str, usage: Usage, turn_id: str, session_id: str | None = None
    ) -> None:
        """Records model usage and costs for a turn (and the session it belongs to)."""
        ...

    async def totals(self, *, session_id: str | None = None) -> Usage:
        """Returns total usage for one session, or across every session when ``session_id`` is None."""
        ...


@runtime_checkable
class Strategy(Protocol):
    name: str  # "single_loop", "architect_editor"

    async def run(self, turn: Any, k: Any) -> TurnResult:
        """Runs turn strategy using TurnContext and KernelServices."""
        ...


@runtime_checkable
class Hook(Protocol):
    name: str
    events: frozenset[HookEvent]
    matcher: str | None  # regex on tool name for tool hooks
    timeout_s: float

    async def on(
        self, event: HookEvent, payload: dict[str, Any], ctx: Any
    ) -> HookOutcome:
        """Invoked when subscribed lifecycle hook fires."""
        ...

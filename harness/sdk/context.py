"""
Context Protocols and Views provided by the Kernel to Plugins.
Plugins receive these interfaces during lifecycle operations and execution turns.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from harness.sdk.contracts import (
    ContextSource,
    Executor,
    Hook,
    Policy,
    Process,
    Provider,
    SandboxBackend,
    Strategy,
    ToolHandler,
)
from harness.sdk.events import EventPayload
from harness.sdk.types import (
    Capability,
    ExecRequest,
    ImageBlock,
    Message,
    ModelRequest,
    SessionInfo,
    TaskInfo,
    ToolCall,
    ToolResult,
    ToolSpec,
    TurnResult,
)


@runtime_checkable
class SettingsView(Protocol):
    def get(self, path: str, default: Any = None) -> Any:
        """Reads namespaced setting e.g. 'plugins.my_plugin.timeout'."""
        ...

    def on_change(self, callback: Callable[[list[str]], None]) -> None:
        """Subscribes callback to setting changes."""
        ...


@runtime_checkable
class SecretsView(Protocol):
    def get(self, name: str, default: str | None = None) -> str | None:
        """Retrieves secret by name. Requires Capability.SECRETS."""
        ...

    def has(self, name: str) -> bool:
        """Checks if secret exists without exposing value."""
        ...


@runtime_checkable
class PluginCommandContext(Protocol):
    """Narrow runtime services available to a dispatched plugin command.

    Commands are not model tools: they do not pass through ``PolicyGate``. Mutating plugin commands therefore
    must use ``request_approval(destructive=True)`` and ``session_lock`` rather than reaching into kernel
    internals or treating an HTTP call as user approval.
    """

    workspace: Path
    settings: SettingsView

    def session_lock(self, session_id: str) -> AbstractAsyncContextManager[None]:
        """Exclusively serialize a command with turns for the named session."""
        ...

    async def read_records(self, session_id: str) -> list[dict[str, Any]]:
        """Return the durable transcript records for a session."""
        ...

    async def request_approval(
        self,
        *,
        session_id: str,
        kind: str,
        summary: str,
        details: dict[str, Any] | None = None,
        destructive: bool = False,
    ) -> tuple[bool, str]:
        """Request a visible, fail-closed approval; destructive requests honor the kernel's blocking setting."""
        ...

    async def emit_record(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        payload: EventPayload,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Append a durable record before publishing its typed event."""
        ...


@runtime_checkable
class JobsView(Protocol):
    """Session-scoped jobs API exposed to tools without exposing the registry internals."""

    async def start(self, executor: Executor, req: ExecRequest, label: str = "") -> dict[str, Any]:
        """Persist a start fact, then launch and monitor a process as a durable job."""
        ...

    def register_process(
        self,
        process: Process,
        label: str = "",
        *,
        initial_output: str = "",
    ) -> str:
        """Adopt an already-started legacy background process and return its job id."""
        ...

    def get(self, job_id: str) -> dict[str, Any] | None:
        ...

    def list(self) -> list[dict[str, Any]]:
        ...

    async def output(
        self,
        job_id: str,
        *,
        tail_lines: int = 50,
        since_offset: int | None = None,
    ) -> dict[str, Any] | None:
        ...

    async def kill(self, job_id: str) -> bool:
        ...

    async def send_input(self, job_id: str, text: str) -> bool:
        ...

    async def wait(self, job_id: str, timeout_s: float | None = None) -> int | None:
        ...


@runtime_checkable
class PluginContext(Protocol):
    name: str
    workspace: Path
    data_dir: Path
    settings: SettingsView
    secrets: SecretsView
    log: logging.Logger
    capabilities: frozenset[Capability]
    sandbox: Any  # dynamic kernel service; backends may register after this context was created

    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None:
        ...

    def unregister_tool(self, name: str) -> bool:
        """Removes a tool this plugin previously registered, returning whether it existed."""
        ...

    def emit_event(
        self,
        payload: EventPayload,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Publishes a typed live event, optionally scoped to a session and turn."""
        ...

    async def emit_record(
        self,
        payload: EventPayload,
        *,
        content: str,
        metadata: dict[str, Any],
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Append a durable fact to the named (or active) session before publishing its event."""
        ...

    def register_service(self, name: str, service: object) -> None:
        """Register a kernel-facing service under a unique name for other kernel components to consume."""
        ...

    def register_command(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        args_schema: dict[str, Any] | None = None,
        exposed: bool = True,
    ) -> None:
        """Register a frontend command as ``plugins.<plugin-id>.<name>``.

        ``name`` is local to this plugin (no dotted aliases). If ``args_schema`` is supplied, the kernel uses it
        both to validate calls and to publish argument names in ``commands.list``.
        """
        ...

    def register_provider(self, p: Provider) -> None:
        ...

    def register_policy(self, p: Policy) -> None:
        ...

    def register_executor(self, e: Executor) -> None:
        ...

    def register_sandbox_backend(self, backend: SandboxBackend) -> None:
        ...

    def register_context_source(self, s: ContextSource) -> None:
        ...

    def register_store(
        self, kind: Literal["transcript", "search", "artifacts", "cost"], s: object
    ) -> None:
        ...

    def register_strategy(self, s: Strategy) -> None:
        ...

    def register_hook(self, h: Hook) -> None:
        ...


@runtime_checkable
class ToolContext(Protocol):
    call_id: str
    session_id: str
    turn_id: str | None
    turn_state: dict[str, Any]
    workspace: Path
    data_dir: Path
    executor: Executor
    capabilities: frozenset[Capability]
    jobs: JobsView | None

    deadline: float | None
    """Loop time at which the gate will stop waiting for this call (WS-03)."""

    def remaining_time(self) -> float:
        """Seconds until the tool deadline; unbounded when called outside a gated tool handler."""
        ...

    async def ask_user(
        self,
        questions: list[dict[str, Any]],
        timeout_s: float | None = None,
    ) -> dict[str, Any] | None:
        """Ask structured questions and wait for an interactive client; ``None`` means unavailable or unanswered."""
        ...

    cancel_event: Any
    """Set by the gate when the deadline passes. A handler that can produce something useful while
    being stopped (partial output, a clean shutdown message) should poll this and return; one that
    cannot just let the cancellation arrive."""

    def emit_output_delta(self, stream: Literal["stdout", "stderr"], text: str) -> None:
        """Streams output delta chunks during execution.

        The kernel also buffers these, so a call that is cancelled mid-flight still has its partial
        output to show the model (WS-03 `test_partial_output_survives_timeout`).
        """
        ...

    async def emit_record(
        self,
        payload: EventPayload,
        *,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Append a durable transcript record before publishing its typed event."""
        ...

    def background(self, proc: Process, label: str = "") -> str:
        """Registers a running process as a background task and returns task_id."""
        ...

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Gets status dictionary for a registered task."""
        ...

    def list_tasks(self) -> list[dict[str, Any]]:
        """Lists all registered tasks."""
        ...

    async def kill_task(self, task_id: str) -> bool:
        """Terminates a registered task."""
        ...

    async def send_task_input(self, task_id: str, text: str) -> bool:
        """Sends stdin input to a registered task."""
        ...

    async def wait_task(self, task_id: str, timeout_s: float | None = None) -> Any:
        """Waits for task completion and returns exit code."""
        ...


@runtime_checkable
class PolicyContext(Protocol):
    workspace: Path
    capabilities: frozenset[Capability]


@runtime_checkable
class TurnContext(Protocol):
    session: SessionInfo
    turn_id: str
    prompt: str
    media: list[ImageBlock]
    options: dict[str, Any]
    settings: SettingsView
    cancel: asyncio.Event

    @property
    def session_id(self) -> str:
        ...

    @property
    def workspace(self) -> Path:
        ...


@runtime_checkable
class HookContext(Protocol):
    session_id: str
    turn_id: str | None
    workspace: Path
    settings: SettingsView | None
    turn_state: dict
    """Scratch space shared by every hook for the duration of one turn (created per turn by the
    runner, never persisted). Repeat detection, read ledgers and other cross-call memory live here
    so a hook needs no module globals — and a restart cannot strand half a turn's state."""

    def emit(self, event_type: str, data: Any) -> None:
        """Publishes a live event on the bus (never durable, never a tool's private channel)."""
        ...

    async def emit_record(
        self,
        payload: EventPayload,
        *,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Append a durable transcript record before publishing its typed event."""
        ...


@runtime_checkable
class KernelServices(Protocol):
    async def assemble_context(self, *, tools: list[ToolSpec] | None = None) -> ModelRequest:
        ...

    async def call_model(self, req: ModelRequest) -> Message:
        ...

    async def execute_tool(self, call: ToolCall) -> ToolResult:
        ...

    async def execute_tools(self, calls: list[ToolCall]) -> list[ToolResult]:
        ...

    async def record(self, msg: Message) -> int:
        ...

    async def wait_for_wakeup(self, timeout_s: float = 600.0) -> Any:
        ...

    def pending_tasks(self) -> list[TaskInfo]:
        ...

    async def compact_if_needed(self) -> bool:
        ...

    async def spawn_child(
        self,
        *,
        strategy: str = "single_loop",
        prompt: str,
        isolation: str = "none",
        inherit_history: bool = False,
    ) -> TurnResult:
        ...

    async def emit(self, event: Any) -> None:
        ...

    def spend_remaining(self) -> float | None:
        ...

    @property
    def tools(self) -> list[ToolSpec]:
        ...


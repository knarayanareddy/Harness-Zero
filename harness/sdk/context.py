"""
Context Protocols and Views provided by the Kernel to Plugins.
Plugins receive these interfaces during lifecycle operations and execution turns.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from harness.sdk.contracts import (
    ContextSource,
    Executor,
    Hook,
    Policy,
    Process,
    Provider,
    Strategy,
    ToolHandler,
)
from harness.sdk.types import (
    Capability,
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
class PluginContext(Protocol):
    name: str
    workspace: Path
    data_dir: Path
    settings: SettingsView
    secrets: SecretsView
    log: logging.Logger
    capabilities: frozenset[Capability]

    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None:
        ...

    def register_provider(self, p: Provider) -> None:
        ...

    def register_policy(self, p: Policy) -> None:
        ...

    def register_executor(self, e: Executor) -> None:
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
    workspace: Path
    data_dir: Path
    executor: Executor
    capabilities: frozenset[Capability]

    def emit_output_delta(self, stream: Literal["stdout", "stderr"], text: str) -> None:
        """Streams output delta chunks during execution."""
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
    workspace: Path


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


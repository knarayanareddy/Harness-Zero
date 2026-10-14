"""
Context Protocols and Views provided by the Kernel to Plugins.
Plugins receive these interfaces during lifecycle operations and execution turns.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from harness.sdk.contracts import (
    ContextSource,
    Executor,
    Hook,
    Policy,
    Provider,
    Strategy,
    ToolHandler,
)
from harness.sdk.types import (
    Capability,
    Message,
    ModelRequest,
    ToolCall,
    ToolResult,
    ToolSpec,
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
    def get(self, name: str) -> str | None:
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


@runtime_checkable
class PolicyContext(Protocol):
    workspace: Path
    capabilities: frozenset[Capability]


@runtime_checkable
class TurnContext(Protocol):
    turn_id: str
    session_id: str
    workspace: Path
    history: list[Message]
    user_prompt: str


@runtime_checkable
class HookContext(Protocol):
    session_id: str
    workspace: Path


@runtime_checkable
class KernelServices(Protocol):
    async def call_model(self, req: ModelRequest) -> Any:
        ...

    async def execute_tools(self, calls: list[ToolCall]) -> list[ToolResult]:
        ...

    async def wait_for_wakeup(self, timeout_s: float = 600.0) -> Any:
        ...

    def spend_remaining(self) -> float | None:
        ...

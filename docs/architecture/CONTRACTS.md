# Harness-Zero Architecture: SDK Contracts & Protocols

This document specifies the structural contracts, runtime interfaces, domain types, and enforcement invariants that define the Harness-Zero SDK (§11).

---

## 1. System Topology & Process Isolation

```
┌────────────────────────────────────────────────────────┐
│                   Frontends                            │
│  - Web Studio (Port 8000 API, Port 8001 Sandbox)       │
│  - CLI (harness interactive / harness run)             │
│  - Agent Client Protocol (harness acp)                 │
└──────────────────────────┬─────────────────────────────┘
                           │ Commands API & EventBus
┌──────────────────────────▼─────────────────────────────┐
│                 Modular Kernel                         │
│  - Registry & Discovery (Tiers: builtin, local, ws)   │
│  - Central Gate (Policy Evaluation: jail, tirith)     │
│  - Turn Runner & Context Assembler                     │
│  - EventBus (Publish / Subscribe / Replay)             │
└──────────────────────────┬─────────────────────────────┘
                           │ KernelServices & Context Views
┌──────────────────────────▼─────────────────────────────┐
│             Modular Plugins (harness/plugins/**)       │
│  - Providers (OpenAI, Anthropic, Gemini, Ollama)       │
│  - Tools (FS, Search, Shell, Memory, Screen)           │
│  - Policies (Jail, Risk Classifier, Tirith, Modes)     │
│  - Context Sources (Identity, Repo Map, Instructions)  │
│  - Executors (Local, Worktree)                         │
│  - Stores (JSONL, FTS5, Artifacts, Cost Ledger)        │
│  - Strategies (Single Loop, Architect/Editor)          │
│  - Bridges & Hooks (MCP Client/Server, Shell Hooks)    │
└────────────────────────────────────────────────────────┘
```

---

## 2. Structural Plugin Protocols (`harness.sdk.contracts`)

All plugin contracts are `@runtime_checkable` `typing.Protocol` classes.

### `Plugin`
Lifecycle entry point for every plugin:
```python
@runtime_checkable
class Plugin(Protocol):
    manifest: PluginManifest

    async def setup(self, ctx: PluginContext) -> None:
        """Called on plugin initialization with PluginContext."""
        ...

    async def teardown(self) -> None:
        """Called on plugin shutdown for resource cleanup."""
        ...
```

### `Provider`
Language model provider abstraction:
```python
@runtime_checkable
class Provider(Protocol):
    name: str
    caps: ProviderCaps

    async def list_models(self) -> list[ModelInfo]:
        """Returns available models supported by this provider."""
        ...

    def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Streams model events for the request. Raises ProviderError on failure."""
        ...

    async def aclose(self) -> None:
        """Closes any underlying network clients or connections."""
        ...
```

### `ToolHandler`
Model-invocable tool implementation:
```python
@runtime_checkable
class ToolHandler(Protocol):
    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Executes tool with validated arguments and ToolContext."""
        ...
```

### `Policy`
Security guardrail and permission evaluator:
```python
@runtime_checkable
class Policy(Protocol):
    name: str
    order: int  # 0..1000; lower runs first (jail=10, classifier=50, tirith=60, modes=900)

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision:
        """Evaluates tool call and returns a Decision (ALLOW, ASK, DENY). Must not raise."""
        ...
```

### `Executor` & `Process`
Command execution sandbox:
```python
@runtime_checkable
class Process(Protocol):
    id: str
    pid: int | None

    def output(self) -> AsyncIterator[tuple[Literal["stdout", "stderr"], str]]: ...
    async def send_input(self, text: str) -> None: ...
    async def kill(self, grace_s: float = 2.0) -> None: ...
    async def wait(self) -> ExecResult: ...

@runtime_checkable
class Executor(Protocol):
    name: str
    isolation: Literal["none", "worktree", "container", "vm"]

    async def start(self, req: ExecRequest) -> Process: ...
```

### `ContextSource`
Prompt assembly component:
```python
@runtime_checkable
class ContextSource(Protocol):
    name: str

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        """Returns context blocks for prompt assembly."""
        ...
```

### `TranscriptStore`
Dual-log append-only transcript storage:
```python
@runtime_checkable
class TranscriptStore(Protocol):
    async def append(self, record: dict[str, Any]) -> int: ...
    async def read(self, since_step: int = 0, compact: bool = True) -> list[dict[str, Any]]: ...
    async def last_compaction(self) -> dict[str, Any] | None: ...
```
A store holds one session; `SessionManager.list_sessions()` (kernel) enumerates sessions.

### `Strategy`
Autonomous orchestration loop strategy:
```python
@runtime_checkable
class Strategy(Protocol):
    name: str

    async def run(self, turn: TurnContext, k: KernelServices) -> TurnResult: ...
```

### `Hook`
Lifecycle event subscriber:
```python
@runtime_checkable
class Hook(Protocol):
    name: str
    events: frozenset[HookEvent]
    matcher: str | None
    timeout_s: float

    async def on(self, event: HookEvent, payload: dict[str, Any], ctx: HookContext) -> HookOutcome: ...
```

---

## 3. Context Views & Kernel Interfaces

Plugins interact with the system strictly through scoped context views:

| Interface | Received By | Methods Provided |
| :--- | :--- | :--- |
| `PluginContext` | `Plugin.setup()` | `register_tool`, `register_provider`, `register_policy`, `register_executor`, `register_context_source`, `register_store`, `register_strategy`, `register_hook` |
| `ToolContext` | `ToolHandler.__call__()` | `call_id`, `workspace`, `data_dir` |
| `PolicyContext` | `Policy.evaluate()` | `workspace`, `settings`, `secrets` |
| `TurnContext` | `Strategy.run()` | `turn_id`, `session_id`, `prompt`, `workspace`, `model`, `provider` |
| `HookContext` | `Hook.on()` | `session_id`, `workspace`, `turn_id` |
| `KernelServices` | `Strategy.run()` | `run_turn_step`, `evaluate_tool`, `call_tool`, `spawn_child` |

---

## 4. Architectural Boundary Invariants

Automated AST tests enforce four strict architectural boundaries:

1. **Plugins Boundary** (`test_import_boundaries.py:51`):
   `harness/plugins/**` must **only import `harness.sdk`**. Any import from `harness.kernel`, `harness.frontends`, or other internal packages is a build failure.
2. **SDK Boundary** (`test_import_boundaries.py:34`):
   `harness/sdk/**` must never import anything from `harness.*` outside `harness.sdk`.
3. **Kernel Boundary** (`test_import_boundaries.py:69`):
   `harness/kernel/**` must never import from `harness.plugins.*`. All plugins are loaded dynamically through manifests and discovery.
4. **Subprocess Boundary** (`test_no_direct_subprocess.py`):
   Tools and plugins must never import `subprocess` directly. Execution must route through `Executor` contracts to guarantee audit logging and sandboxing.

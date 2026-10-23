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
    isolation: Literal["none", "worktree", "container", "vm", "os-sandbox"]

    async def start(self, req: ExecRequest) -> Process: ...

@runtime_checkable
class SandboxBackend(Protocol):
    name: str

    def available(self) -> SandboxAvailability: ...
    def wrap(self, req: ExecRequest, policy: SandboxPolicy) -> ExecRequest: ...
```

`SandboxAvailability` (`backend`, `available`, `reason`, `enforcement`) is a probed host fact, not a configuration
claim. `SandboxPolicy` supplies the workspace, its writable paths, explicit read-only host paths, network permission,
and an environment allowlist. The registry owns the live sandbox backend slot; every `ctx.executor` is wrapped at
the gate, so a worktree or third-party executor cannot bypass it. `required` + unavailable denies before the
executor starts; `preferred` + unavailable asks before an unconfined fallback; the process result reports the
actual enforcement in `ExecResult.metadata.sandbox`. Only the SDK-owned OS backend implementation probes the host
with `subprocess`; plugins and tools still route their commands through `Executor`.

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

## 5. Baseline boundary contract v1

Added by T03. The sections above describe the SDK's existing structural surface;
the following behavioral requirements are acceptance targets. Their known
violations are tracked in `../testing/review_regressions.json`, not implied fixed
by this document. `CONSTRAINTS.md` at the repository root owns numerical gates.

| ID | Required behavior | Live owner | Current evidence / repair owner |
|---|---|---|---|
| BC-01 | Every filesystem target opened by a tool remains inside the resolved workspace, including discovered paths and symlinks; processes additionally obey configured native confinement | jail, search/fs tools, gate | R1; T04; `test_R01_locked_search_cannot_read_outside_symlink` |
| BC-02 | The canonical request preserves typed text, media, tool calls and results. Adapters normalize ToolResultBlock independently of its carrier role. ImageBlock has `media_type`, `data_b64`, `url`, never invented `.data`. Unsupported media fails visibly; it is never replaced by Success | SDK types, context derivation, providers, MCP handler | R2/R6/R12; T12/T13/T22; named probes R02/R06/R12 |
| BC-03 | Turn cancellation reaches pending interactions and active owned work; cancellation is rechecked before starting a handler. Tool timeout remains separate and cannot cancel another session. Unknown external outcomes are not represented as rollback | Commands.cancel_turn, runner, gate, interactions, executors | R3; T05/T06; R03 process probe; pending-approval proof required |
| BC-04 | Bridges invoke actual Commands/PolicyGate signatures; headless approval is fail-closed. MCP empty allow-list exports nothing, canonical settings govern both discovery AND invocation. ACP conformance is pinned to an actually tested external version | CLI, MCP/ACP bridges, gate | R4/R5/R13; T17–T20; actual CLI probes R04/R05/R13 |
| BC-05 | All session-scoped events/actions carry and enforce session identity; history replies must still belong to the active generation. Reload reconciles pending/busy state from the kernel, not stale UI assumptions | Studio app/events and Commands session state | R7; T23/T24; R07 jsdom probe; browser proof required |
| BC-06 | Sandbox wrapping preserves validated requested cwd and reports probed enforcement. Required + unavailable never runs unconfined. Approval cannot turn unavailable confinement into full confinement | sandbox service/backends, executor | R8; T11; R08 wrapper probe; native proof required |
| BC-07 | A valid provider terminal sequence precedes successful Done. SSE errors, premature EOF and transport errors are typed failures; arbitrary chunk boundaries preserve valid calls. Error kinds use SDK literals, not nonexistent enum members | providers/parsers, runner/breaker | R9/R10/R14; T14–T16; probes R09/R10/R14 |
| BC-08 | A successful initial/background MCP handshake publishes its discovered catalogue to the kernel exactly once per change; connected status alone is not registration | MCP client/plugin registry callback | R11; T21; R11 late-start stdio probe |
| BC-09 | Real compaction uses Hook.events/on with current timeout semantics and a complete ModelRequest including params; summary is appended before completion, then consumed by actual request derivation; original log retained | compactor, runner, context, transcript | R15; T09/T10; real threshold probe R15; later layers also require tests |
| BC-10 | Acknowledged records remain readable after process restart, partial dual-log failure cannot swallow a later append, every dangling call repairs durably and idempotently without re-execution | transcript/store/session | R16/R17; T07/T08; probes R16/R17; session-log addendum |
| BC-11 | Declared resources exist in the delivered package and can be discovered without the checkout. Wheel skills and frozen Studio manifest are part of the product | package config/resource loaders/PyInstaller | R18/R19; T25/T26; recipe and wheel-inventory probes, stronger install/native proof required |

### Canonical and wire representations are distinct

`derive_messages` currently emits USER-carried ToolResultBlock values. That is a
canonical SDK representation, not an OpenAI wire requirement. Do not globally
change it to TOOL to patch one adapter and break another. Each adapter owns its
provider representation, matches every result's `tool_use_id` to the original
call, and preserves advertised media. Provider request-body tests must cross the
real adapter, not duplicate its conversion in a helper.

Real kernel method names are `Commands.submit_turn`, `cancel_turn`,
`sessions_create`, `sessions_list`, and `PolicyGate.execute`. A mock offering
obsolete names does not prove compatibility. Hook contract is `events`/`on`,
not singular `event`/`execute`. `ModelRequest.params` is required. Existing
`KernelServices.spawn_child` declarations do not establish a live supported
subagent execution path.

### Capability support is a tested tuple

Support is `(artifact, OS/runtime, provider/client version, capability, evidence)`.
Native tools advertised on an unavailable platform, a vision capability whose
payload builder fails, or an initialized bridge that cannot run a session are
not supported combinations. Record unverified external versions rather than
inventing a compatibility claim; see `../testing/VERIFICATION_MATRIX.md`.

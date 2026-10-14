# Harness-Zero — Modular Harness Architecture (v1 design)

**Status:** design for implementation · **Companion docs:** `REMEDIATION_PLAN.md` (v2, sequenced build plan), `REMEDIATION_FINDINGS.md` (per-finding repros/fix specs), `CODE_REVIEW.md` (evidence).

**Goal.** A coding-agent harness whose *kernel* is small, boring and stable, and whose every user-visible capability — model providers, tools, safety policy, execution backends, prompt context, storage, orchestration strategies, lifecycle hooks and frontends — is a *plugin* against a published, versioned, machine-testable contract. The intended workflow is: a user points their own agent/LLM at a feature from another harness (Claude Code hooks, Aider's repo map, Cline checkpoints, OpenHands' Docker runtime, Codex's `AGENTS.md`, …), the agent scaffolds a plugin, implements it against the contract, and iterates until the conformance kit is green.

---

## 0. Design principles (binding)

1. **Kernel owns invariants, plugins own features.** The kernel owns: canonical types, the turn lifecycle, the policy choke point, context assembly, the event stream, settings, and plugin loading. Nothing vendor-specific lives in the kernel (e.g. "cache stability" is a kernel concept; Anthropic `cache_control` is a provider's business).
2. **One import surface.** Plugins may import **only** `harness.sdk`. Enforced by a test (§9.4). The kernel may import the SDK; the SDK imports nothing from the kernel or plugins.
3. **Every tool call passes through the gate.** Plugins *register* tools; they never *dispatch* them. Jail, policy chain, approvals and hooks run in the kernel's `PolicyGate` for every call regardless of which plugin registered the tool (§5.3).
4. **Frontends consume events; plugins never touch frontends.** Plugins emit typed events and *cards* (§6, §7). Each frontend renders card kinds it knows and falls back deterministically for ones it doesn't.
5. **Second-implementation rule.** A seam is only promoted to a contract once two real implementations exist or are scheduled in the plan. Everything else stays private to the kernel.
6. **Honesty over surface.** No simulated behaviour behind a real-looking surface. A capability that is not implemented is absent from tool schemas, settings and UI.
7. **Hermetic and offline by default.** No CDN, no network in tests, no reading other tools' secret files.
8. **Semver on the SDK; the kernel refuses incompatible plugins.** Additions are minor; removals/renames are major.

---

## 1. System overview

```
┌──────────────────────────────── Frontends ────────────────────────────────┐
│  CLI (rich)   │  Web studio (aiohttp + static ESM)  │  ACP (editors)  │ macOS launcher │
│        consume EventBus (typed events + cards)  ·  call Commands             │
└───────────────────────────────────┬───────────────────────────────────────┘
                                    │  Commands / Events (in-process or HTTP+SSE)
┌───────────────────────────────────▼───────────────────── Kernel ───────────┐
│ Session  · Registry  · SettingsStore  · Secrets  · EventBus                 │
│ TurnRunner ──► Strategy.run(TurnContext, KernelServices)                     │
│   KernelServices: assemble_context · call_model · execute_tool(→PolicyGate)  │
│                   wait_for_wakeup · compact_if_needed · spawn_child           │
│ PolicyGate: hooks(pre) → policy chain → jail → approvals → Tool → hooks(post)│
│ ContextAssembler (stability tiers) · Compaction · BackgroundInbox · Approvals │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┬────┘
       │          │          │          │          │          │          │
   Provider     Tool      Policy    Executor  ContextSrc    Store    Strategy   Hook
   plugins     plugins    plugins    plugins    plugins     plugins   plugins  plugins
       │                                                                      │
   HTTP APIs                        External processes: MCP servers · hook scripts · ACP clients
```

**Nine extension points** (contracts): `Provider`, `Tool`, `Policy`, `Executor`, `ContextSource`, `Store` (4 sub-protocols), `Strategy`, `Hook`, `Frontend` (a documented command/event surface rather than a Python Protocol). Plus `Bridge` plugins, which are ordinary plugins that translate an external protocol (MCP, ACP, shell hooks) into those nine.

---

## 2. Target repository layout

```
harness/
  sdk/                      # THE plugin import surface. Semver'd. No kernel imports.
    __init__.py             # __version__ = "1.0.0"; re-exports
    types.py                # Message, ContentBlock*, ToolSpec, ToolCall, ToolResult, Usage, RiskClass, ...
    contracts.py            # Protocols: Plugin, Provider, ToolHandler, Policy, Executor, ContextSource, *Store, Strategy, Hook
    context.py              # PluginContext, ToolContext, TurnContext, PolicyContext, HookContext (interfaces only)
    events.py               # Event dataclasses + to_json/schema()
    cards.py                # Card kinds
    manifest.py             # PluginManifest (TOML loader + validation)
    errors.py               # HarnessError hierarchy
    testing/                # Conformance kits (pytest plugins) — see §11
      provider_kit.py tool_kit.py policy_kit.py executor_kit.py context_kit.py
      store_kit.py strategy_kit.py hook_kit.py frontend_kit.py scripted_provider.py
  kernel/
    registry.py             # discovery, manifest validation, capability clamping, enable/disable
    session.py              # Session: workspace dirs, stores, settings, bus, locks, inbox, approvals
    runner.py               # TurnRunner + KernelServices implementation
    gate.py                 # PolicyGate (the choke point)
    assembler.py            # ContextAssembler (stability tiers → ModelRequest)
    compaction.py
    inbox.py                # BackgroundInbox (task registry + wake-ups)
    approvals.py            # ApprovalBroker
    settings.py             # SettingsStore (namespaced JSON, schema registry, atomic writes)
    secrets.py              # key storage (0600) + env precedence
    bus.py                  # EventBus (fan-out, bounded queues, delta coalescing)
    commands.py             # Command surface used by all frontends
    jail.py                 # WorkspaceJail
    errors.py
  plugins/                  # first-party plugins; each is a directory with plugin.py + harness_plugin.toml + tests/
    providers/   openai_compat/ anthropic/ gemini/ ollama/ mock/
    tools/       fs/ shell/ search/ memory/ skills/ artifacts/ screenshot_macos/
    policy/      workspace_jail/ risk_classifier/ tirith/ permission_modes/
    executors/   local/ worktree/
    context/     identity/ tool_guidance/ skills_catalog/ memories/ repo_map/ instructions_files/
    stores/      jsonl_transcript/ fts5_search/ fs_artifacts/ cost_ledger/
    strategies/  single_loop/ architect_editor/
    hooks/       shell_hooks/ checkpoints/
    bridges/     mcp_client/ mcp_server/ acp/
  frontends/
    cli/          app.py renderers.py
    web/          server.py  static/{index.html, app.js, api.js, events.js, cards.js, settings.js, styles.css, vendor/}
  experimental/   battle_arena.py spawn_ledger.py state_graph.py concurrency_gate.py browser_cdp.py visual_grounding.py
  cli.py          # entry point (thin)
builtin/skills/   # unchanged (SKILL.md packs)
launcher/         # Swift app (unchanged location)
packaging/macos/  # icon, pyinstaller spec, script
docs/
tests/            # kernel + integration + e2e; plugin tests live next to plugins
```

Rules: a plugin directory contains exactly one `harness_plugin.toml`; plugin tests import `harness.sdk.testing`; `harness/kernel/**` never imports `harness/plugins/**` (registry loads by manifest `entry` string); `harness/frontends/**` imports kernel `commands`/`bus` only.

---

## 3. Core types — `harness/sdk/types.py`

Full signatures. `@dataclass(slots=True)` throughout; all JSON-serialisable via `to_dict()/from_dict()`.

```python
SDK_API_VERSION = "1"                      # manifest api_version must equal this

class Role(str, Enum): SYSTEM="system"; USER="user"; ASSISTANT="assistant"; TOOL="tool"

@dataclass(frozen=True) class TextBlock:      text: str;  type: Literal["text"] = "text"
@dataclass(frozen=True) class ImageBlock:     media_type: str; data_b64: str | None = None; url: str | None = None; type: Literal["image"] = "image"
@dataclass(frozen=True) class ThinkingBlock:  text: str; signature: str | None = None; redacted: bool = False; type: Literal["thinking"] = "thinking"
@dataclass(frozen=True) class ToolUseBlock:   id: str; name: str; input: dict[str, Any]; type: Literal["tool_use"] = "tool_use"
@dataclass(frozen=True) class ToolResultBlock: tool_use_id: str; content: list[TextBlock | ImageBlock]; is_error: bool = False; type: Literal["tool_result"] = "tool_result"
ContentBlock = TextBlock | ImageBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock

@dataclass class Message:
    role: Role
    content: list[ContentBlock]
    name: str | None = None                 # participant / tool name
    metadata: dict[str, Any] = field(default_factory=dict)   # step_index, ts, ephemeral=True for non-persisted context
    def text(self) -> str                   # concatenated TextBlocks

class RiskClass(str, Enum):
    READ = "read"                 # no side effects beyond returning data
    WRITE = "write"               # mutates files inside the workspace
    EXEC = "exec"                 # runs processes (inside workspace cwd)
    NETWORK = "network"           # outbound network
    DESTRUCTIVE = "destructive"   # irreversible, history-rewriting, or outside-workspace effects
RISK_ORDER = [READ, WRITE, EXEC, NETWORK, DESTRUCTIVE]      # max() semantics for escalation

@dataclass(frozen=True) class ToolAnnotations:               # MCP-compatible hints
    read_only: bool = False; destructive: bool = False; idempotent: bool = False; open_world: bool = False; title: str | None = None

@dataclass class ToolSpec:
    name: str                                # ^[a-z][a-z0-9_]{1,63}$  (model-facing; namespaced tools use "ns__name")
    description: str
    input_schema: dict[str, Any]             # JSON Schema object; kernel validates args before dispatch
    risk_class: RiskClass = RiskClass.READ   # static upper bound declared by the tool; policies may raise per call
    capabilities: frozenset[str] = frozenset()   # what the handler needs from ToolContext: "fs:read","fs:write","exec","net","secrets","approval"
    annotations: ToolAnnotations = ToolAnnotations()
    namespace: str | None = None             # e.g. "mcp:filesystem"
    plugin: str = ""                         # set by registry
    timeout_s: float = 600.0
    streams_output: bool = False             # emits tool.output.delta events

@dataclass class ToolCall:   id: str; name: str; arguments: dict[str, Any]
@dataclass class ToolResult:
    content: list[TextBlock | ImageBlock]
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)   # conventional keys: changed_files, task_id, exit_code, truncated, bytes
    cards: list[Card] = field(default_factory=list)
    @classmethod def text(cls, s: str, **metadata) -> ToolResult
    @classmethod def error(cls, s: str, **metadata) -> ToolResult

@dataclass class Usage:
    input_tokens: int = 0; output_tokens: int = 0; cache_read_tokens: int = 0; cache_write_tokens: int = 0
    reasoning_tokens: int = 0; cost_usd: float | None = None     # None = unknown pricing (never guess)
    def __add__(self, other: Usage) -> Usage

class Stability(str, Enum): STATIC="static"; SESSION="session"; TURN="turn"
@dataclass class ContextBlock:
    id: str                      # unique per source, e.g. "identity", "skills_catalog", "repo_map"
    text: str
    stability: Stability
    priority: int = 100          # lower first within a tier
    source: str = ""             # plugin name (set by assembler)
    token_estimate: int = 0      # set by assembler if 0

@dataclass class ModelParams:
    temperature: float | None = None; max_tokens: int | None = None; reasoning_effort: str | None = None
    stop: list[str] = field(default_factory=list); extra: dict[str, Any] = field(default_factory=dict)   # provider-specific passthrough

@dataclass class ModelRequest:
    model: str
    system: list[ContextBlock]     # STATIC then SESSION blocks, in order; provider maps tier boundaries to cache hints
    messages: list[Message]        # history (+ ephemeral TURN block message, see §5.4)
    tools: list[ToolSpec]
    params: ModelParams
    stream: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)   # session_id, turn_id (for logs; never sent)

# Provider output is a stream of events (non-streaming providers yield a handful at once)
@dataclass class TextDelta:      text: str
@dataclass class ThinkingDelta:  text: str
@dataclass class ToolCallStart:  id: str; name: str
@dataclass class ToolCallDelta:  id: str; arguments_json: str          # partial JSON
@dataclass class ToolCallEnd:    id: str; name: str; arguments: dict[str, Any]
@dataclass class UsageEvent:     usage: Usage
@dataclass class ProviderNotice: message: str                           # e.g. "fell back to model X"
@dataclass class Done:           stop_reason: Literal["end_turn","tool_use","max_tokens","stop_sequence","cancelled"]
ModelEvent = TextDelta | ThinkingDelta | ToolCallStart | ToolCallDelta | ToolCallEnd | UsageEvent | ProviderNotice | Done

@dataclass(frozen=True) class ProviderCaps:
    native_tools: bool; streaming: bool; vision: bool; cache_hints: bool; thinking: bool
    json_output: bool = False; max_context_tokens: int | None = None

@dataclass class ModelInfo: id: str; display_name: str; context_tokens: int | None = None; input_usd_per_mtok: float | None = None; output_usd_per_mtok: float | None = None; caps: ProviderCaps | None = None

class Action(str, Enum): ALLOW="allow"; ASK="ask"; DENY="deny"
@dataclass class Decision:
    action: Action; reason: str = ""; risk: RiskClass | None = None
    arguments: dict[str, Any] | None = None       # normalised/replacement arguments (applied in policy order)
    policy: str = ""                               # set by gate
    @staticmethod def allow(reason="") / ask(reason, risk=None) / deny(reason, risk=None)

@dataclass class ExecRequest:
    command: str | list[str]; cwd: Path; env_overlay: dict[str, str] = field(default_factory=dict)
    timeout_s: float | None = 600; interactive: bool = False; shell: bool = True
@dataclass class ExecResult: exit_code: int | None; stdout: str; stderr: str; truncated: bool; duration_ms: int

class HookEvent(str, Enum):
    SESSION_START="session_start"; USER_PROMPT_SUBMIT="user_prompt_submit"; PRE_TOOL_USE="pre_tool_use"
    POST_TOOL_USE="post_tool_use"; PRE_COMPACT="pre_compact"; STOP="stop"; TURN_ERROR="turn_error"; NOTIFICATION="notification"
@dataclass class HookOutcome:
    action: Literal["continue","block","mutate"] = "continue"
    reason: str = ""; arguments: dict[str, Any] | None = None; additional_context: str | None = None

class Capability(str, Enum): FS_READ="fs:read"; FS_WRITE="fs:write"; EXEC="exec"; NET="net"; SECRETS="secrets"; APPROVAL="approval"; APPROVAL_BYPASS="approval:bypass"; UI="ui"

@dataclass class TurnResult:
    output: str; thinking: str = ""; usage: Usage = field(default_factory=Usage)
    tool_calls: int = 0; stop_reason: str = "end_turn"; error: str | None = None; cards: list[Card] = field(default_factory=list)
```

**Errors — `sdk/errors.py`:** `HarnessError` → `ProviderError(provider, model, status: int|None, retryable: bool, kind: Literal["auth","rate_limit","server","timeout","bad_request","unavailable"])`, `PathOutsideWorkspace`, `CapabilityDenied`, `ToolInputError`, `ApprovalDenied`, `SpendCapExceeded`, `PluginLoadError`, `IncompatiblePlugin`.

---

## 4. Contracts — `harness/sdk/contracts.py`

All are `typing.Protocol` (structural) with `@runtime_checkable`. Async everywhere.

```python
class Plugin(Protocol):
    manifest: PluginManifest
    async def setup(self, ctx: PluginContext) -> None: ...     # register things; may read ctx.settings
    async def teardown(self) -> None: ...

class Provider(Protocol):
    name: str                                   # "anthropic"
    caps: ProviderCaps
    async def list_models(self) -> list[ModelInfo]: ...
    def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]: ...   # raises ProviderError; never raw httpx errors
    async def aclose(self) -> None: ...

class ToolHandler(Protocol):
    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...
# registered as ctx.register_tool(spec: ToolSpec, handler: ToolHandler)

class Policy(Protocol):
    name: str
    order: int                                  # 0..1000; lower runs first (jail=10, classifier=50, tirith=60, modes=900)
    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision: ...   # must not raise

class Executor(Protocol):
    name: str                                   # "local", "worktree", "docker"
    isolation: Literal["none","worktree","container","vm"]
    async def start(self, req: ExecRequest) -> Process: ...
class Process(Protocol):
    id: str; pid: int | None
    def output(self) -> AsyncIterator[tuple[Literal["stdout","stderr"], str]]: ...
    async def send_input(self, text: str) -> None: ...
    async def kill(self, grace_s: float = 2.0) -> None: ...
    async def wait(self) -> ExecResult: ...

class ContextSource(Protocol):
    name: str
    async def blocks(self, ctx: TurnContext) -> list[ContextBlock]: ...   # STATIC blocks must be byte-identical for the session

class TranscriptStore(Protocol):
    async def append(self, record: TranscriptRecord) -> int: ...          # returns step_index
    async def read(self, since_step: int = 0, compact: bool = True) -> list[TranscriptRecord]: ...
    async def last_compaction(self) -> TranscriptRecord | None: ...
    async def list_sessions(self) -> list[SessionInfo]: ...
class SearchIndex(Protocol):
    async def index(self, session_id: str, record: TranscriptRecord) -> None: ...
    async def search(self, query: str, *, scope: Literal["session","workspace"] = "workspace", session_id: str | None = None, limit: int = 10) -> list[SearchHit]: ...
class ArtifactStore(Protocol):
    async def write(self, name: str, content: bytes | str, *, media_type: str, meta: dict) -> ArtifactRef: ...
    async def read(self, name: str) -> tuple[bytes, ArtifactRef]: ...
    async def list(self) -> list[ArtifactRef]: ...
class CostLedger(Protocol):
    async def record(self, *, model: str, provider: str, usage: Usage, turn_id: str) -> None: ...
    async def totals(self, *, session_id: str | None = None) -> Usage: ...

class Strategy(Protocol):
    name: str                                   # "single_loop", "architect_editor"
    async def run(self, turn: TurnContext, k: KernelServices) -> TurnResult: ...

class Hook(Protocol):
    name: str
    events: frozenset[HookEvent]
    matcher: str | None                         # regex on tool name for *_TOOL_USE events
    timeout_s: float
    async def on(self, event: HookEvent, payload: dict[str, Any], ctx: HookContext) -> HookOutcome: ...
```

**Kernel-provided interfaces — `sdk/context.py`** (plugins receive these; never construct them):

```python
class PluginContext(Protocol):
    name: str; workspace: Path; data_dir: Path         # <workspace>/.harness/plugins/<name>/
    settings: SettingsView                             # namespaced read/subscribe; .get(path, default) .on_change(cb)
    secrets: SecretsView                               # .get("OPENAI_API_KEY") (requires Capability.SECRETS)
    log: logging.Logger
    capabilities: frozenset[Capability]                # clamped; see §9
    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None
    def register_provider(self, p: Provider) -> None
    def register_policy(self, p: Policy) -> None
    def register_executor(self, e: Executor) -> None
    def register_context_source(self, s: ContextSource) -> None
    def register_store(self, kind: Literal["transcript","search","artifacts","cost"], s: object) -> None
    def register_strategy(self, s: Strategy) -> None
    def register_hook(self, h: Hook) -> None
    def register_settings_schema(self, schema: dict) -> None   # JSON Schema; namespaced under plugins.<name>
    def register_command(self, name: str, handler) -> None     # frontend-invocable, e.g. "mcp.reconnect"
    async def emit(self, event: Event) -> None                 # requires Capability.UI for ui.card

class ToolContext(Protocol):
    call: ToolCall; spec: ToolSpec; session: SessionInfo; turn_id: str
    workspace: Path; jail: Jail                        # jail.resolve(path, for_write=False) -> Path (raises PathOutsideWorkspace)
    executor: Executor                                 # CapabilityDenied unless "exec" granted
    settings: SettingsView; secrets: SecretsView; log: logging.Logger
    stores: StoreAccess                                # .transcript .search .artifacts .cost (read-mostly)
    cancel: asyncio.Event
    async def emit(self, event: Event) -> None
    async def stream_output(self, stream: str, text: str) -> None     # → tool.output.delta
    async def ask(self, summary: str, details: dict, *, kind: str = "tool") -> bool   # requires "approval"
    def background(self, proc: Process, *, label: str) -> str          # registers with inbox; returns task_id

class TurnContext(Protocol):
    session: SessionInfo; turn_id: str; prompt: str; media: list[ImageBlock]
    options: dict[str, Any]                            # provider, model, strategy, files, test_command, auto_approve ...
    settings: SettingsView; cancel: asyncio.Event
class KernelServices(Protocol):
    async def assemble_context(self, *, tools: list[ToolSpec] | None = None) -> ModelRequest
    async def call_model(self, req: ModelRequest) -> Message        # streams events, accumulates assistant Message, records usage/cost, enforces spend cap
    async def execute_tool(self, call: ToolCall) -> ToolResult      # PolicyGate
    async def execute_tools(self, calls: list[ToolCall]) -> list[ToolResult]   # parallel when all are READ, else sequential
    async def record(self, msg: Message) -> int                      # transcript append (+ search index)
    async def wait_for_wakeup(self, timeout_s: float) -> WakeupEvent | None
    def pending_tasks(self) -> list[TaskInfo]
    async def compact_if_needed(self) -> bool
    async def spawn_child(self, *, strategy: str, prompt: str, isolation: str = "none", inherit_history: bool = False) -> TurnResult   # depth-limited
    async def emit(self, event: Event) -> None
    tools: list[ToolSpec]
class PolicyContext(Protocol):  session: SessionInfo; workspace: Path; jail: Jail; executor_isolation: str; settings: SettingsView; permission_mode: str; prior: list[Decision]
class HookContext(Protocol):    session: SessionInfo; workspace: Path; settings: SettingsView; log: logging.Logger
```

---

## 5. Kernel behaviour

### 5.1 Boot
1. `SettingsStore` loads `<workspace>/.harness/settings.json` (namespaced: `kernel.*`, `plugins.<name>.*`); defaults from registered schemas.
2. `Registry.discover()` (order: builtin → entry points `harness.plugins` → `~/.harness/plugins/*` → `<workspace>/.harness/plugins/*`), validates manifests (`api_version == SDK_API_VERSION`, compat ranges, platform), applies enable/disable and capability clamping (§9), calls `setup()` in dependency order (`requires` in manifest). Load failures are reported as `plugin.error` events and the plugin is skipped — never a crash, never silent.
3. Session opens stores (one `TranscriptStore` per session dir; `SearchIndex`, `ArtifactStore`, `CostLedger` per workspace), writes `/.harness/` to `.git/info/exclude` when applicable, fires `SESSION_START` hooks.

### 5.2 Turn lifecycle (sequence)
```
Frontend ─ submit_turn(session, prompt, media, options)
  Session.lock(session)  (busy → 409 TurnInProgress)
  hooks USER_PROMPT_SUBMIT (block → turn.error; additional_context → TURN block)
  record user Message                           → transcript, search index
  Strategy.run(TurnContext, KernelServices)      (default: single_loop)
    loop (≤ kernel.max_tool_iterations):
      req = assemble_context()                  (§5.4)
      compact_if_needed()                       (§5.5; may rebuild req)
      assistant = call_model(req)               (events: model.*; usage → cost ledger; spend cap)
      if no tool_use blocks: break (unless pending background tasks and wait policy → wait_for_wakeup)
      results = execute_tools(tool_use blocks)  (§5.3)
      record(assistant), record(tool results)
      if wakeup arrived: record(wakeup message)
  hooks STOP (block → one more iteration with reason as context, max 2 blocks)
  turn.completed  / turn.error / turn.cancelled
```
`Strategy` may deviate (architect_editor runs two model roles and a test loop) but **must** only reach models and tools via `KernelServices` — that is what keeps policy and accounting universal.

### 5.3 PolicyGate.execute(call) — the choke point
1. Resolve `spec` by name (unknown → `ToolResult.error("unknown tool")`, event `tool.result`). Validate `arguments` against `input_schema` (invalid → error result with the validator message; never dispatch).
2. `PRE_TOOL_USE` hooks whose `matcher` matches. `block` → error result "blocked by hook <name>: <reason>" and event `tool.decision{action:deny, policy:"hook:<name>"}`. `mutate` → arguments replaced. Timeout → treated as `continue` with a `log` warning (configurable to block).
3. Policy chain in `order`: each returns a `Decision`; `arguments` replacements apply cumulatively; effective risk = max(spec.risk_class, all `decision.risk`). Combination: any `DENY` → deny (first reason); else any `ASK` → ask (reasons joined); else allow. Policies see `prior` decisions.
4. Permission mode (policy plugin `permission_modes`, order 900): `plan` → deny anything with effective risk > READ; `allow` → ALLOW for WRITE/EXEC inside workspace, ASK for DESTRUCTIVE; `ask` → ASK for WRITE/EXEC/NETWORK/DESTRUCTIVE unless `kernel.security.destructive_blocking` denies DESTRUCTIVE outright; `bypass` (only if `approval:bypass` capability is held by the *session principal*, never by third-party plugins) → ALLOW.
5. `ASK` → `ApprovalBroker.ask()` → event `approval.required`; resolution via command `resolve_approval`; timeout `kernel.security.approval_timeout_s` → deny ("timed out"). Non-interactive turns (`options.auto_approve` absent and no frontend subscribed with `interactive=True`) → deny with "non-interactive session".
6. Build `ToolContext` with services gated by `spec.capabilities ∩ plugin.capabilities` (`executor` raises `CapabilityDenied` otherwise).
7. Run handler under `asyncio.timeout(spec.timeout_s)` and cancellation; any exception → `ToolResult.error(f"{type}: {msg}")` + `log` event with traceback (never propagates into the turn).
8. `POST_TOOL_USE` hooks (may append `additional_context` → appended as a TextBlock to the result; may mark `block` → result flagged `is_error` with reason).
9. Record `tool.result`; sanitise secrets in text (Tirith `sanitize_output` as a kernel utility); truncate to `kernel.limits.tool_result_bytes` (default 64 KiB; full text persisted to the transcript `full` log).

**Invariants (tested in `tests/kernel/test_gate_invariants.py`):** no handler runs without steps 1–6; a `DENY` from any policy wins; jail resolution happens before any handler sees a path (the `workspace_jail` policy normalises `path`-typed arguments — fields named in `spec.input_schema` with `"format": "workspace-path"`); hooks and policies cannot raise into the turn; `approval:bypass` cannot be granted via manifest.

### 5.4 Context assembly
- Collect `ContextBlock`s from every `ContextSource` (parallel, each under a 5 s timeout; a failing source contributes nothing and logs).
- Tier ordering: `STATIC` (identity, tool guidance, instruction files) → `SESSION` (skills catalog, memories, repo map hash-stable within a session) → `TURN` (timestamp, pending tasks, hook `additional_context`, approval state).
- `system = STATIC + SESSION` blocks; `messages = history + [ephemeral user Message with TURN blocks wrapped in `<context>…</context>`] + latest user message`. Ephemeral messages are never persisted.
- History = transcript records after the last compaction summary (summary first), converted to `Message`s with real `ToolUseBlock`/`ToolResultBlock` pairs (compact text for old turns, full text for the current turn).
- Provider adapters map tier boundaries to cache hints (Anthropic: `cache_control` on the last STATIC block and last SESSION block; OpenAI: nothing to do — prefix caching is automatic when the prefix is stable; Gemini: context caching only when explicitly configured).
- `tools` are **not** serialised into any block when `provider.caps.native_tools` is true; the `tool_guidance` source emits usage rules only. The XML tool protocol is a provider-internal fallback (`ollama` plugin) and emits its own block.
- Invariance rule (checked by `context_kit`): a source's STATIC blocks are byte-identical across two assemblies in the same session; SESSION blocks change only when their declared inputs change (sources expose `fingerprint()` for the kit).

### 5.5 Compaction
Trigger: `estimate_tokens(system + messages) > kernel.context.budget_tokens * 0.8` (estimator = provider's `count_tokens` if available else `len/4`). Steps: `PRE_COMPACT` hooks → summarisation request via the same provider, no tools, fixed prompt producing `## Goal / ## Decisions / ## State of work / ## Open items` → `TranscriptRecord(type=COMPACTION_SUMMARY)` appended → events `compaction.started/completed`. Records are never dropped except at a compaction boundary.

### 5.6 Background tasks and wake-ups
`ToolContext.background(proc, label)` registers a `Process` with `BackgroundInbox`, which monitors it and on exit enqueues `WakeupEvent(task_id, exit_code, tail)` and emits `task.completed`. `single_loop` policy: if the model stops with pending tasks and the last tool result in this turn created one, `wait_for_wakeup(kernel.background.wait_s, default 600)`; a wake-up is recorded as a `Message(role=user, content=[TextBlock("Background task <id> (<label>) finished with exit <code>:\n<tail>")], metadata={"wakeup": task_id})` and the loop continues. `manage_task` tool (shell plugin) offers `status|wait|kill|send_input` for explicit control.

### 5.7 Settings & secrets
- `settings.json` is namespaced; plugins register JSON Schema fragments; the store validates writes and exposes `SettingsView` (read-only, path-based, change subscriptions). Frontends render settings forms **from the registered schemas** (`x-ui` extension keys: `title`, `group`, `widget`, `secret`), which is how the settings UI stays honest — only real settings exist.
- `kernel.*` schema (defaults): `provider`, `model`, `strategy="single_loop"`, `max_tool_iterations=20`, `security.permission_mode="ask"`, `security.destructive_blocking=true`, `security.approval_timeout_s=300`, `security.spend_cap_usd=5.0`, `context.budget_tokens=60000`, `background.wait_s=600`, `limits.tool_result_bytes=65536`, `limits.upload_bytes=26214400`, `plugins.enabled/disabled`, `plugins.allow_inprocess_third_party=false`, `plugins.trusted_workspace_plugins=false`.
- `Secrets`: `~/.harness/secrets.json` (0600) ∪ process env (env wins only when `kernel.secrets.env_precedence=true`; default false so a stray `OPENROUTER_API_KEY` cannot silently change behaviour). Never returned by commands; `has(key)` only.

### 5.8 Concurrency & cancellation
One turn per session (lock → `TurnInProgress`). `cancel_turn` sets `TurnContext.cancel` and cancels the model stream; in-flight tools see `ctx.cancel`; background processes keep running unless `kill_on_cancel=true`. Cancellation records a `SYSTEM_NOTIFICATION` and emits `turn.cancelled`. All subprocess/sqlite work is async or in `asyncio.to_thread`.

---

## 6. Event stream — `harness/sdk/events.py`

Envelope: `{"v": 1, "id": "evt_01J…", "ts": 1760400000.123, "session_id": "…", "turn_id": "…|null", "type": "…", "data": {…}}`. `harness events schema` emits the JSON Schema for all types (generated from the dataclasses; committed to `docs/events.schema.json` and checked in CI for drift).

| Type | `data` fields | Notes |
|---|---|---|
| `session.started` / `session.switched` | `session_id, workspace, strategy, provider, model` | |
| `plugin.loaded` / `plugin.error` | `name, version, provides[], capabilities[], trust` / `+ error` | |
| `turn.started` | `prompt_preview, strategy, provider, model, options` | |
| `turn.completed` | `stop_reason, usage, duration_ms, tool_calls` | |
| `turn.cancelled` / `turn.error` | `reason` / `message, kind, retryable` | |
| `context.assembled` | `blocks:[{id,stability,source,tokens}], total_tokens, history_messages` | debug level; UI "context inspector" |
| `model.started` | `provider, model, request_tokens_estimate` | |
| `model.text.delta` / `model.thinking.delta` | `text` | coalescable |
| `model.tool_call` | `call_id, name, arguments` | after `ToolCallEnd` |
| `model.notice` | `message` | fallbacks, truncation |
| `model.completed` | `stop_reason, usage` | |
| `tool.call` | `call_id, name, arguments, risk, plugin` | before policy |
| `tool.decision` | `call_id, action, reason, policy, risk` | one per call |
| `approval.required` | `approval_id, call_id|null, kind, summary, details, timeout_s, options` | |
| `approval.resolved` | `approval_id, approved, by, reason` | |
| `tool.output.delta` | `call_id, stream, text` | coalescable |
| `tool.result` | `call_id, name, is_error, preview, metadata, duration_ms` | preview ≤ 2 KiB |
| `task.started` / `task.completed` | `task_id, label, pid` / `+ exit_code, tail` | |
| `compaction.started` / `compaction.completed` | `before_tokens` / `+ after_tokens, step_index` | |
| `cost.updated` | `turn_usd, session_usd, cap_usd, unknown_pricing: bool` | |
| `ui.card` | `card` (§7) | |
| `hook.fired` | `hook, event, outcome, duration_ms` | |
| `settings.changed` | `paths[]` | |
| `log` | `level, plugin, message` | |

Bus semantics: async fan-out, per-subscriber bounded queue (1 000); on overflow `*.delta` events for the same `call_id`/turn are coalesced, other events are never dropped (the subscriber is marked `lagging` and receives a `log` warning). Subscribers declare `interactive: bool` (used by §5.3 step 5).

---

## 7. UI cards — `harness/sdk/cards.py`

A card is `{"kind": str, "id": str, "title": str|null, ...}`. Built-in kinds and required fields:

| kind | fields | CLI rendering | Web rendering |
|---|---|---|---|
| `markdown` | `body` | rich Markdown | sanitised Markdown |
| `code` | `language, body, path?` | syntax panel | highlighted block |
| `diff` | `path, unified, stats{added,removed}` | coloured diff | side-by-side/unified |
| `table` | `columns[], rows[][]` | table | table |
| `notice` | `level: info|warn|error, body` | coloured line | banner |
| `progress` | `label, value 0-1|null` | spinner/bar | bar |
| `tree` | `nodes[{label, children[], meta}]` | indented | collapsible |
| `approval` | `approval_id, summary, details, options[]` | prompt | buttons |
| `artifact` | `name, media_type, url, request_feedback: bool` | path + summary | md → sanitised; html → **sandbox-origin iframe only** |
| `form` | `form_id, schema (JSON Schema), submit_label` | questionary prompts | generated form → command `form.submit` |
| `x-<plugin>-<kind>` | plugin-defined | fallback | fallback |

Fallback (mandatory in every frontend, tested by `frontend_kit`): unknown kind → render `title` + pretty-printed JSON in a monospace block. All text fields are untrusted: Markdown passes through a sanitiser; never `innerHTML` raw strings.

---

## 8. Plugin manifest and discovery

`harness_plugin.toml`:
```toml
[plugin]
name = "repo-map"                     # ^[a-z][a-z0-9-]{1,40}$ ; unique across the registry
version = "0.1.0"
api_version = "1"                     # must equal harness.sdk.SDK_API_VERSION
summary = "Aider-style repository map as a SESSION context block"
entry = "harness.plugins.context.repo_map.plugin:RepoMapPlugin"   # importable path
provides = ["context"]                # provider|tool|policy|executor|context|store|strategy|hook|bridge|command
capabilities = ["fs:read"]            # requested; clamped by trust tier and settings
requires = []                         # other plugin names that must load first
platforms = ["darwin", "linux"]       # optional

[settings]
schema = "settings.schema.json"       # optional; namespaced under plugins.repo-map

[compat]
harness = ">=0.2,<0.3"
python = ">=3.11"
```
Discovery sources and resulting trust tier: `builtin` (shipped in `harness/plugins`), `local` (`~/.harness/plugins/<name>/`, Python entry points installed by the user), `workspace` (`<workspace>/.harness/plugins/<name>/`, loaded only if `kernel.plugins.trusted_workspace_plugins` is true — like trusting a folder), `third_party` (entry points from distributions not on the local allow-list). Name collisions (plugins, tools, strategies) fail loudly at load with both origins named; tools from bridges are always namespaced (`mcp__<server>__<tool>`).

CLI: `harness plugin list|info|enable|disable|new|test|doctor`.

---

## 9. Trust and security model

### 9.1 Tiers
| Tier | How it runs | May provide | Capabilities grantable |
|---|---|---|---|
| `builtin` | in-process | everything | all except `approval:bypass` (bypass is a *session principal* right, set by the user per session, never a plugin right) |
| `local` | in-process | everything | as requested, minus `approval:bypass`; `secrets` prompts once per plugin on first enable |
| `workspace` | in-process, only if trusted | everything | as `local` |
| `third_party` | **out of process by default** via bridges (MCP for tools/resources, shell hooks for lifecycle, ACP for frontends); in-process only when `kernel.plugins.allow_inprocess_third_party=true` | tools/hooks/frontends (bridged); any kind if in-process allowed | never `secrets`, never `approval:bypass` |

### 9.2 Capability clamping
Effective capabilities = manifest `capabilities` ∩ tier allowance ∩ `kernel.plugins.capabilities[<name>]` (user override). `PluginContext` services raise `CapabilityDenied` outside the effective set; the registry refuses to register a `ToolSpec` whose `capabilities` exceed the plugin's effective set.

### 9.3 What the gate guarantees regardless of plugin quality
Workspace jail on every path-typed argument; executor cwd and env scrub (`*_API_KEY|*_TOKEN|*SECRET*|*PASSWORD*|*CREDENTIAL*|AWS_*|GH_TOKEN|GITHUB_TOKEN` removed; `PAGER=cat GIT_PAGER=cat CI=1 HARNESS_AGENT=1 TERM=dumb`); approvals for risky actions; output secret scrubbing; per-call timeout; spend cap; no raw exceptions reach the model.

### 9.4 Boundary enforcement (tests, not conventions)
- `tests/kernel/test_import_boundaries.py`: AST scan — `harness/plugins/**` imports only `harness.sdk` (+ stdlib/third-party); `harness/sdk/**` imports no `harness.*`; `harness/kernel/**` imports no `harness.plugins.*`; `harness/frontends/**` imports only `harness.kernel.commands`, `harness.kernel.bus`, `harness.sdk`.
- `tests/kernel/test_no_direct_subprocess.py`: tool plugins do not import `subprocess`/`asyncio.subprocess` (they must use `ctx.executor`); exemptions listed explicitly (executors, bridges).
- `tests/kernel/test_gate_invariants.py` (§5.3).

### 9.5 Transport security (web frontend)
Per-launch bearer token embedded in the served page and required as `X-Harness-Token` on all non-public routes; Host allow-list; `Origin`/`Sec-Fetch-Site` checks; JSON-only bodies; CSP on both origins; artifact HTML only on the sandbox origin with `connect-src 'none'`; sanitised Markdown; vendored JS, no CDN. (Details: `REMEDIATION_FINDINGS.md` SEC-01…SEC-05.)

---

## 10. Protocol bridges (first-party plugins)

| Bridge | Direction | Maps to | Notes |
|---|---|---|---|
| `mcp_client` | external MCP servers → Harness | `tools/list` → `ToolSpec` (namespace `mcp:<server>`, annotations → risk: `read_only→READ`, `destructive→DESTRUCTIVE`, `open_world→NETWORK`, else `WRITE`); `resources` → `read_resource` tool + optional `ContextSource` for pinned resources; `prompts` → frontend slash-commands | stdio first, streamable-HTTP second; servers configured in `plugins.mcp-client.servers[]`; status surfaced via `plugin.loaded/error` and command `mcp.status` |
| `mcp_server` | Harness → other MCP hosts | exposes registered tools (filtered by policy and by `plugins.mcp-server.expose[]`) over stdio | lets Claude Code / Cursor / Zed call Harness tools and policies |
| `shell_hooks` | user scripts → lifecycle | **Claude Code-compatible** config: `{"hooks": {"PreToolUse": [{"matcher": "shell_run|fs_write", "hooks": [{"type": "command", "command": "…", "timeout": 60}]}]}}`; payload on stdin as JSON; exit `2` = block (stderr = reason); stdout JSON `{"decision": "block|approve", "reason", "updatedInput", "additionalContext"}` | event-name aliases `PreToolUse→pre_tool_use` etc.; lets users reuse hooks they already wrote |
| `acp` | editors → Harness | Agent Client Protocol (JSON-RPC over stdio): session/new, prompt, cancel, tool-call permission requests ↔ `approval.*`, content updates ↔ `model.*`/`tool.*` | a *frontend*, not a tool source |

---

## 11. Conformance kits — `harness/sdk/testing/`

Each kit is a pytest plugin exposing a base class; a plugin's `tests/test_conformance.py` subclasses it and supplies a fixture. `harness plugin test <path>` runs them. Two modes: `recorded` (default, fixtures only, no network) and `live` (`HARNESS_LIVE=1` plus credentials).

| Kit | Checks (must all pass) |
|---|---|
| `ProviderKit` | `list_models` returns ≥1 or raises `ProviderError(kind="auth")`; plain prompt yields ≥1 `TextDelta`, exactly one `Done`, one `UsageEvent`; tool round-trip (forced tool call → `ToolCallEnd` with parsed dict → follow-up with `ToolResultBlock` yields text); streaming yields ≥2 deltas when `caps.streaming`; HTTP 401/429/500/timeouts map to `ProviderError` with correct `kind`/`retryable`; no raw `httpx` exceptions escape; `aclose` idempotent; cache hints: second identical request reports `cache_read_tokens > 0` (live only, when `caps.cache_hints`); request bodies never contain `metadata`. |
| `ToolKit` | `input_schema` is valid JSON Schema and has `additionalProperties: false`; invalid args rejected by the gate before the handler runs; every path argument declared with `"format": "workspace-path"`; escape attempts (`../`, absolute, symlink) return error results, never exceptions; honours `ctx.cancel` within 2 s; declared `risk_class` ≥ observed effects (run in a temp workspace: wrote files ⇒ ≥ WRITE; spawned process ⇒ ≥ EXEC); result ≤ `tool_result_bytes` with `truncated` metadata; no `subprocess` import. |
| `PolicyKit` | deterministic for identical input; never raises; returns `Decision`; `order` within 0..1000; `arguments` replacements are schema-valid. |
| `ExecutorKit` | echo round-trip; cwd enforced; env scrub (plant `FOO_API_KEY`, run `env`); 1 MiB single-line output; timeout kills; `kill` is idempotent and final status is `killed`; `interactive` input works; `isolation` matches behaviour (container: `/etc/hostname` differs). |
| `ContextKit` | STATIC blocks byte-identical across two calls; SESSION blocks stable when `fingerprint()` unchanged; ids unique; respects `max_tokens` hint; completes < 5 s on a 2 000-file fixture repo. |
| `StoreKit` | append/read round-trip; truncated trailing line tolerated; concurrent appends from two tasks keep ordering; search returns the inserted record; artifact names validated; cost totals add up. |
| `StrategyKit` | with `ScriptedProvider`: completes a two-tool turn; emits `turn.started` … `turn.completed` in order; honours cancel; never calls a provider or tool except via `KernelServices` (kit injects spies). |
| `HookKit` | `continue/block/mutate` semantics; timeout → logged; payload schema per event. |
| `FrontendKit` | renders every built-in card kind and an unknown kind without throwing (CLI: snapshot; web: headless JS test via `node --test` on `cards.js`). |

`ScriptedProvider` (in the SDK) is the only mock: it replays a list of `ModelEvent` sequences and asserts on the requests it receives. There is no heuristic mock anywhere.

---

## 12. Migration map (current → target)

| Current file (LOC) | Target | Action | Carry over / notes (v1 finding IDs) |
|---|---|---|---|
| `core/types.py` (132) | `sdk/types.py` | **rewrite** | keep `StepType/StepSource` enums for transcript records |
| `core/transcript.py` (214) | `plugins/stores/jsonl_transcript/` | **port** | add `call_id/tool_name/is_error` metadata, tolerate bad lines (MDL-03, LOOP-05); `compact_trajectory` → kernel `compaction.py` |
| `core/coordinator.py` (661) | `kernel/runner.py` + `kernel/gate.py` + `plugins/strategies/single_loop/` + tool plugins | **split & rewrite** | `_dispatch_tool` branches → `plugins/tools/{fs,shell,search,memory,skills,artifacts,screenshot_macos}`; `execute_turn` → `single_loop`; inbox → `kernel/inbox.py` (LOOP-01); MCP auto-registration deleted (SIM-01) |
| `core/daemon.py` (845) | `frontends/web/server.py` + `kernel/commands.py` | **split & rewrite** | routes become `Commands` + one SSE stream of the EventBus; middlewares from SEC-01..03; `_get_cowork_settings` → schema-generated settings (TOOL-06); fake account/MCP removed (SEC-09) |
| `core/concurrency_gate.py` (151) | `experimental/` | **quarantine** | reconsider as a `Policy` ("max parallel EXEC") later |
| `models/router.py` (663) | `plugins/providers/{openai_compat,anthropic,gemini,ollama,mock}/` | **split & rewrite** | per-adapter native tools (MDL-03), errors (MDL-01), timeouts (MDL-02), usage (MDL-06), fallback policy (MDL-05); `_mock_agent_turn` **deleted** → `ScriptedProvider` |
| `models/prefix_cache.py` (76) | `kernel/assembler.py` + `plugins/context/{identity,tool_guidance}` | **port** | tiers → `Stability`; tools not serialised when native (CONF-04) |
| `models/circuits.py` (130) | `kernel/runner.py` (breaker per provider/model) | **port minimal** | `classify_intent`/tier tables → `experimental/` (MDL-07) |
| `models/cost_tracker.py` (112) | `plugins/stores/cost_ledger/` | **port** | pricing table per provider+model; `None` = unknown (MDL-06) |
| `models/reasoning_stream.py` (193) | `plugins/providers/*/` (thinking blocks) | **port pieces** | XML `<tool_call>` parser → `ollama` fallback only |
| `sandboxing/executor.py` (212) | `plugins/executors/local/` | **port & harden** | TOOL-05 (env scrub, DEVNULL stdin, chunked reads, kill race, no mkdir) |
| `sandboxing/guardrails.py` (111) | `plugins/policy/risk_classifier/` | **port & extend** | TOOL-04 patterns; token-based protected paths |
| `sandboxing/tirith_security.py` (163) | `plugins/policy/tirith/` + `kernel/gate.py` (`sanitize_output`) | **port & wire** | add RFC1918/127.0.0.0/8 to network targets |
| `sandboxing/diff_applier.py` (83) | `plugins/strategies/architect_editor/diffs.py` + `plugins/tools/fs` (`edit_file`) | **rewrite** | file-headed SEARCH/REPLACE blocks (ORCH-01) |
| `sandboxing/worktree.py` (90) | `plugins/executors/worktree/` | **port** | async git, base commit recorded (ORCH-03) |
| `sandboxing/ripgrep.py` (115) | `plugins/tools/search/` | **port** | drop ChatGPT.app path; regex semantics aligned (HYG-03) |
| `memory/fts5_index.py` (200) | `plugins/stores/fts5_search/` | **port** | workspace-scoped DB, `scope` param (LOOP-06) |
| `memory/repo_map.py` (133) | `plugins/context/repo_map/` | **port** | SESSION block with fingerprint (LOOP-07) |
| `memory/semantic_memories.py` (86) | `plugins/context/memories/` + `plugins/tools/memory` (`remember`) | **port** | |
| `skills/registry.py` (136) | `plugins/context/skills_catalog/` + `plugins/tools/skills` (`view_skill`) | **port** | fix `Dict[str, any]` |
| `skills/mcp_bridge.py` (69) | `plugins/bridges/mcp_client/` | **rewrite** | real stdio JSON-RPC (SIM-01) |
| `artifacts/manager.py` (105) | `plugins/stores/fs_artifacts/` + `plugins/tools/artifacts` | **port** | name validation, no nested dir (SEC-06, TOOL-07); HTML served by web frontend sandbox origin only |
| `orchestration/architect_editor.py` (200) | `plugins/strategies/architect_editor/` | **rewrite** | via `KernelServices` only; no auto-commit; file-scoped staging with approval (ORCH-01) |
| `orchestration/battle_arena.py` (151), `spawn_ledger.py` (239), `state_graph.py` (136) | `experimental/` | **quarantine** | `KernelServices.spawn_child` is the future home (ORCH-02/04) |
| `browser/stagehand_engine.py` (140), `desktop/macos_bridge.py` (72) | `experimental/browser_cdp.py`, `plugins/tools/screenshot_macos/` (screenshot only) | **quarantine / trim** | UI-TARS stub deleted (SIM-02/03) |
| `web/index.html` (3 187) | `frontends/web/static/{index.html, app.js, api.js, events.js, cards.js, settings.js, styles.css, vendor/}` | **split & rewrite** | no build step (ES modules); cards renderer; schema-generated settings; token fetch wrapper; sanitiser (UI-01..09, SEC-04/05) |
| `cli.py` (273), `engine_main.py` (16) | `cli.py` (thin) + `frontends/cli/` | **port** | `--provider` default from settings; approvals prompt; `plugin` subcommands |
| `launcher/HarnessZeroApp.swift` (227) | unchanged location | **fix** | workspace picker, no `$HOME` default (HYG-03) |
| `scripts/comprehensive_features_verification.py` (543) | — | **delete** | SIM-04 |
| `tests/*` (25 files) | `tests/kernel`, `tests/integration`, `tests/e2e`, per-plugin `tests/` | **rewrite incrementally** | hermetic fixtures (HYG-01/02); red-team tests become gate integration tests (CONF-12) |
| `builtin/skills/*` | unchanged | keep | consumed by `skills_catalog` |
| `docs/*` | `docs/` + `docs/architecture/` | **rewrite status** | relative links; status column (DOC-01) |

---

## 13. Porting recipes (for the user's agent)

### 13.1 Generic recipe
1. **Classify the feature** using the table in §13.3 → one (sometimes two) contracts.
2. **Read** `harness/sdk/contracts.py`, the relevant kit in `harness/sdk/testing/`, and the first-party reference plugin of that kind.
3. `harness plugin new --kind <kind> <name>` → scaffold with `plugin.py`, `harness_plugin.toml`, `settings.schema.json`, `README.md`, `tests/test_conformance.py`.
4. **Write the README first**: what it does, what it reads/writes, the capabilities it needs, which settings it adds, and which card kinds it emits.
5. **Implement** against the contract. Vendor specifics stay inside the plugin. Emit cards, never HTML. Use `ctx.executor` for processes, `ctx.jail` for paths, `ctx.secrets` for keys.
6. `harness plugin test ./<name>` until green; add feature tests beside the conformance test.
7. `harness plugin enable <name>`; run the E2E scenario that exercises the feature; add a row to `docs/PLUGIN_CATALOG.md`.

### 13.2 Worked examples
| Feature | Contract(s) | Sketch |
|---|---|---|
| **Claude Code hooks** | none — use `shell_hooks` bridge | Put the existing `hooks` JSON under `plugins.shell-hooks.hooks` in `settings.json`. Event names and payloads are compatible; `matcher` matches Harness tool names (`shell_run`, `fs_write`, `fs_edit`, …). |
| **Aider repo map** | `ContextSource` (+ optional `tree_sitter` extra) | `blocks()` returns one SESSION block ≤ `max_tokens` setting; `fingerprint()` = hash of tracked file mtimes; ranking by symbol references; when fingerprint changes, the block changes (cache miss accepted, documented). |
| **Cline checkpoints** | `Hook` (PRE_TOOL_USE on `fs_write|fs_edit|shell_run`) + `Tool` (`checkpoint_restore`) | Shadow repo at `.harness/checkpoints/` (`git --git-dir=… --work-tree=<ws> add -A && commit`); each checkpoint emits a `diff` card; restore requires approval (`risk_class=DESTRUCTIVE`). |
| **OpenHands Docker runtime** | `Executor` (`isolation="container"`) | `docker run --rm -v <ws>:<ws> -w <cwd> --network=<setting> <image> sh -c …`; env overlay only; a companion `Policy` may downgrade EXEC→WRITE when `ctx.executor_isolation == "container"`. |
| **Codex `AGENTS.md` / Claude `CLAUDE.md` / Cursor rules** | `ContextSource` (`instructions_files`, first-party) | STATIC block from root→cwd chain; setting `files=["AGENTS.md","CLAUDE.md",".cursorrules"]`. |
| **SWE-agent windowed viewer** | `Tool` pack | `open_file(path, line)`, `scroll(direction)`, `goto(line)` with a 100-line window held in plugin state keyed by session. |
| **Goose/MCP ecosystem** | none — `mcp_client` bridge | add `{name, command, args}` to `plugins.mcp-client.servers`. |
| **Devin-style planner** | `Strategy` | plan → approve (`form` card) → execute via `single_loop` child turns (`spawn_child`). |
| **Continue slash-commands** | `register_command` + frontend | commands appear in `/` menus of CLI and web automatically. |

### 13.3 Classification table
| If the feature… | Contract |
|---|---|
| talks to a model API | `Provider` |
| gives the model a new action | `Tool` (or an MCP server via `mcp_client`) |
| decides whether an action may run / needs approval | `Policy` |
| changes *where* commands run (container, VM, remote, worktree) | `Executor` |
| adds text to the prompt (rules, maps, memories, docs) | `ContextSource` |
| persists or searches something | `*Store` |
| changes the shape of a turn (plan/execute, multi-model, retries, subagents) | `Strategy` |
| intercepts lifecycle points (lint on edit, snapshot before write, notify on stop) | `Hook` |
| renders or controls the agent from somewhere new | frontend over Commands/Events (or `acp`) |

---

## 14. Versioning & compatibility

- `harness.sdk.__version__` follows semver; `SDK_API_VERSION` is its major. Manifest `api_version` must equal it.
- **Minor** releases may add: optional fields with defaults, new event types, new card kinds, new optional Protocol methods (checked with `hasattr`). **Major** releases may remove/rename.
- Deprecations: emit `DeprecationWarning` + `log` event for ≥ 1 minor before removal; `harness plugin doctor` lists deprecated usages.
- Event envelope `v` and `docs/events.schema.json` are checked for drift in CI.
- Settings migrations: `kernel/settings.py` keeps a `schema_version` and ordered migration functions.

---

## 15. Non-goals and anti-patterns

- No plugin imports another plugin (share via the SDK or kernel services).
- No "god plugin" that owns the loop; strategies are small compositions over `KernelServices`.
- No HTML or JS from plugins; cards only.
- No direct `subprocess` in tools; executors only.
- No reading of secrets from env or other tools' files inside plugins; `ctx.secrets` only.
- No simulated fallbacks when a backend is unavailable; raise `ProviderError`/`ToolResult.error`.
- No contract "for later": until a second implementation is scheduled, keep it private.
- The kernel does not know about Anthropic, OpenAI, git, Docker, MCP or HTML.

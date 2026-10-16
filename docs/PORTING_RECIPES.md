# Harness-Zero Plugin Porting Recipes

Comprehensive guide and code recipes for creating, porting, and testing modular plugins in the Harness-Zero architecture (§11, §13).

---

## 1. Architectural Philosophy & Boundaries

Harness-Zero enforces strict modular isolation:
1. **SDK Only**: Plugins in `harness/plugins/**` (and third-party plugins in `~/.harness/plugins` or workspace directories) must **only import `harness.sdk`** and the standard library.
2. **No Direct Kernel Coupling**: Plugins never import `harness.kernel`, `harness.frontends`, or other plugins directly.
3. **Capability Sandboxing**: Plugins declare required capabilities (`fs:read`, `fs:write`, `exec`, `net`, `secrets`, `approval`). The kernel gate enforces these capabilities before granting access.
4. **No Direct Subprocess Imports**: Tools and plugins must execute processes through `ctx.executor` or an `Executor` implementation.

---

## Recipe 1: Authoring a Tool Plugin

Tools allow agents to perform actions in the workspace (filesystem operations, searches, code execution).

### Optional: `[ui]` — a feature that owns its own surface (WS-05)

```toml
[ui]
# A side panel, mounted by any frontend that asks `ui.contributions`. `command` must be dispatchable:
# a tab with no command behind it is how a "coming soon" placeholder is born.
panels = [
  { id = "vault", title = "Vault", command = "obsidian.search", renderer = "table" },
]
# Record kinds this plugin's *client module* paints, so the timeline can show them after a reload too.
record_renderers = ["ui.card"]
# Optional, and only for a built-in or config-dir plugin (workspace plugins may not run code in the browser).
client_module = "static/plugins/obsidian.js"   # mounted at /static/plugins/obsidian.js (WS-05c serves plugin
# static dirs; until then the client logs the failed import and the panel keeps its generic renderer)
```

The client module exports two maps, and `app.js` reads them by name after importing the URL the kernel reported:

```js
// static/plugins/obsidian.js — the shape `HarnessStudioApp.loadPluginModules()` expects
export const panels = {
  vault: {
    host: 'panel-vault',                     // optional: defaults to the host the shell creates for the id
    async render({ api, host, escape, select, app }) {
      const rows = (await api.command('obsidian.search', { query: '' })).rows || [];
      host.innerHTML = rows.map((r) => `<div class="panel-item">${escape(r.title)}</div>`).join('');
    },
  },
};
export const renderers = {
  // kind -> (app, record) => void; `app` owns the shared painters (appendNotice, appendCard, appendToolCall, …)
  'obsidian.note': (app, rec) => app.appendNotice(rec.content || '', 'info'),
};
```

Two authoring rules inside a panel module, both learned the hard way:

* **Write a `data-command` value literally.** `harness catalog gen` reads bindings out of the source text, so
  `data-command="${enabled ? 'plugins.disable' : 'plugins.enable'}"` is invisible to `--check` while
  `data-command="plugins.disable"` on one branch and `data-command="plugins.enable"` on the other are both
  gated. Prefer two branches with literal attributes over one interpolated name.
* **Escape what goes in an attribute.** `escape` (textContent-based) defuses text nodes only; a `"` in
  `data-args` ends the attribute. `ctx.escapeAttr` exists for values written into markup — pass
  `JSON.stringify(args)` through it.

A panel key with no declaration, or a renderer for a kind the kernel does not write, is a test failure, not a
feature: the ids must match (`test_the_panel_modules_are_the_declared_panels`), and an excuse in `NO_RENDERER`
may not coexist with a renderer. `render` may be async and may throw — the registry logs it per panel, so one
broken pane never takes the page down.

The parser (`harness/sdk/manifest.py`) refuses an unknown `renderer` (the built-ins are
`table | tree | markdown | list`), a malformed or duplicate panel id, an empty `command`, an unknown `[ui]` key,
and a `client_module` outside `static/`. Settings tabs need no declaration at all: name a group in your
`settings.schema.json` (`x-ui.group`, optionally `x-ui.order`) and it becomes a tab. Saying nothing is also
fine — your properties land on the tab for your plugin's family directory (`hooks` → `Hooks`,
`bridges` → `Bridges & Integrations`, `providers` → `Models & Providers`, …; see
`harness/kernel/settings_schema.py:FAMILY_TABS`), and each property is rendered under a heading naming *your*
plugin (`x-ui.owner`/`owner_title`/`owner_summary` are filled in for you). An unknown family falls back to
`Plugins & Integrations`, which is deliberately kept nearly empty — see `tests/kernel/test_settings_groups.py`.
See
`docs/testing/SCENARIOS.md` for the tests, `docs/catalog/UI_CONTRACT.md` for what the client mounts.

### `harness_plugin.toml`
```toml
[plugin]
name = "my-tool"
version = "0.1.0"
api_version = "1"
summary = "Workspace file validator tool"
provides = ["tool"]
capabilities = ["fs:read"]
entry = "plugin:MyToolPlugin"

[settings]
schema = "settings.schema.json"
```

### `plugin.py`
```python
from __future__ import annotations
from typing import Any
from harness.sdk.context import PluginContext, ToolContext
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import RiskClass, ToolResult, ToolSpec

class ValidatorTool:
    spec = ToolSpec(
        name="validate_file",
        description="Validates JSON syntax for a workspace file",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "format": "workspace-path",
                    "description": "Relative path to target file",
                }
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
        risk_class=RiskClass.READ,
    )

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        rel_path = args["file_path"]
        target = ctx.workspace / rel_path
        if not target.exists():
            return ToolResult(
                call_id=ctx.call_id,
                name="validate_file",
                output=f"File not found: {rel_path}",
                is_error=True,
            )
        return ToolResult(
            call_id=ctx.call_id,
            name="validate_file",
            output=f"File {rel_path} is valid.",
        )

class MyToolPlugin:
    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        tool = ValidatorTool()
        ctx.register_tool(tool.spec, tool)

    async def teardown(self) -> None:
        pass
```

### `tests/test_conformance.py`
```python
import pytest
from harness.sdk.testing import ToolKit
from plugin import ValidatorTool

def test_tool_conformance():
    kit = ToolKit()
    tool = ValidatorTool()
    kit.test_input_schema_valid_and_closed(tool.spec)
    kit.test_workspace_path_formatting(tool.spec)
    kit.test_declared_risk_class_bound(tool.spec)
    kit.test_no_direct_subprocess_import(ValidatorTool)
```

---

## Recipe 2: Authoring an LLM Provider Plugin

Providers connect Harness-Zero to model backends (OpenAI, Anthropic, Gemini, Ollama, local weights).

### `plugin.py`
```python
from __future__ import annotations
from collections.abc import AsyncIterator
from harness.sdk.contracts import Provider
from harness.sdk.context import PluginContext
from harness.sdk.types import (
    Done, ModelEvent, ModelInfo, ModelRequest,
    ProviderCaps, TextDelta, Usage, UsageEvent
)

class CustomProvider(Provider):
    name = "custom-llm"
    caps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=False,
        cache_hints=True,
        thinking=False,
    )

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key

    def is_configured(self) -> bool:
        # The provider is the only authority on its readiness: the kernel lists your models as
        # "needs key" when this is False instead of guessing from secret names.
        return bool(self.api_key)

    async def is_reachable(self) -> bool | None:
        # Hosted API → None (not probed). A local server should GET a cheap endpoint with a
        # ~300 ms timeout, cache the verdict ~30 s and never raise (see the Ollama provider).
        return None

    async def list_models(self) -> list[ModelInfo]:
        # The catalogue you *know* — returned with or without a key, no network call when unconfigured.
        return [
            ModelInfo(
                id="custom-model-v1",
                display_name="Custom Model v1",
                context_tokens=128000,
                input_usd_per_mtok=1.50,
                output_usd_per_mtok=6.00,
            )
        ]

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        # Stream text deltas followed by Usage and Done
        yield TextDelta(text="Hello, ")
        yield TextDelta(text="how can I help you today?")
        yield UsageEvent(usage=Usage(input_tokens=25, output_tokens=10))
        yield Done(stop_reason="end_turn")

    async def aclose(self) -> None:
        pass

class CustomProviderPlugin:
    def __init__(self, manifest=None):
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_provider(CustomProvider(api_key=ctx.secrets.get("CUSTOM_API_KEY", "") or ""))

    async def teardown(self) -> None:
        pass
```

### `tests/test_conformance.py`
```python
import pytest
from harness.sdk.testing import ProviderKit
from plugin import CustomProvider

@pytest.mark.asyncio
async def test_provider_conformance():
    kit = ProviderKit()
    provider = CustomProvider(api_key="test-key")
    await kit.test_readiness(provider)
    await kit.test_list_models(provider)
    await kit.test_plain_prompt(provider)
    await kit.test_streaming_multiple_deltas(provider)
    await kit.test_aclose_idempotent(provider)
```

---

## Recipe 3: Authoring a Context Source Plugin

Context sources inject knowledge, repo maps, instructions, and memories into the prompt assembler.

### Prompt Caching Tiers (`Stability`)
- `Stability.STATIC`: Never changes across turns or sessions (System identity, core instructions). Enables 90%+ prompt cache hit rates.
- `Stability.SESSION`: Stable within a session (Repository map, loaded skills, tool definitions).
- `Stability.TURN`: Dynamic per turn (Current workspace state, active turn query).

### `plugin.py`
```python
from __future__ import annotations
from typing import Any
from harness.sdk.contracts import ContextSource
from harness.sdk.context import PluginContext
from harness.sdk.types import ContextBlock, Stability

class CodingStyleContextSource(ContextSource):
    name = "coding-style"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        return [
            ContextBlock(
                id="style:formatting",
                stability=Stability.STATIC,
                text="Follow PEP 8 formatting and enforce type annotations everywhere.",
            )
        ]

class CodingStylePlugin:
    def __init__(self, manifest=None):
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_context_source(CodingStyleContextSource())

    async def teardown(self) -> None:
        pass
```

---

## Recipe 4: Authoring a Policy / Guardrail Plugin

Policies evaluate tool calls and return a `Decision` (`Action.ALLOW`, `Action.ASK`, `Action.DENY`).

### Ordering
- Lower order runs first:
  - `order = 10`: Jail / Path Traversal guards.
  - `order = 50`: Risk Classifiers.
  - `order = 60`: Tirith Security Shield (SSRF / secrets).
  - `order = 900`: User permission modes (`plan`, `ask`, `bypass`).

### `plugin.py`
```python
from __future__ import annotations
from typing import Any
from harness.sdk.contracts import Policy
from harness.sdk.context import PluginContext
from harness.sdk.types import Action, Decision, ToolCall, ToolSpec

class DangerousCommandGuard(Policy):
    name = "dangerous-command-guard"
    order = 55

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: Any) -> Decision:
        cmd = str(call.arguments.get("command", ""))
        if "rm -rf /" in cmd or ":(){:|:&};:" in cmd:
            return Decision(
                action=Action.DENY,
                reason="Destructive command pattern detected.",
                policy=self.name,
            )
        return Decision(
            action=Action.ALLOW,
            reason="Command safe.",
            policy=self.name,
        )

class DangerousGuardPlugin:
    def __init__(self, manifest=None):
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_policy(DangerousCommandGuard())

    async def teardown(self) -> None:
        pass
```

---

## Recipe 5: Authoring a Lifecycle Hook Plugin

Hooks intercept events like `PRE_TOOL_USE`, `POST_TOOL_USE`, `USER_PROMPT_SUBMIT`, and `STOP`.

```python
from __future__ import annotations
from typing import Any
from harness.sdk.contracts import Hook
from harness.sdk.context import PluginContext
from harness.sdk.types import HookEvent, HookOutcome

class AuditLoggingHook(Hook):
    name = "audit-logger"
    events = frozenset([HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE])
    matcher = None
    timeout_s = 5.0

    async def on(self, event: HookEvent, payload: dict[str, Any], ctx: Any) -> HookOutcome:
        tool_name = payload.get("tool_name", "unknown")
        # Perform non-blocking logging or validation
        return HookOutcome(action="continue", reason="Logged")

class AuditHookPlugin:
    def __init__(self, manifest=None):
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_hook(AuditLoggingHook())

    async def teardown(self) -> None:
        pass
```

---

## 2. Testing and Conformance Tooling

### Scaffolding a New Plugin
```bash
harness plugin new --kind context my_rules
harness plugin new --kind tool my_linter
```

### Running Conformance Tests
```bash
# Run tests for a specific plugin
harness plugin test ./my_rules

# Run all plugin conformance tests
harness plugin test --all

# Run live model tests (opt-in)
harness plugin test ./my_provider --live
```

### Replaying and Recording Model Streams
A provider plugin's own tests should not need a key, and a golden test should not need a live model either.
Both are in the SDK (`harness.sdk.testing`), which is where the scenario suite's fixtures live now:

```python
from harness.sdk.testing import RecordingProvider, ReplayProvider, ScriptedProvider, load_streams

provider = ReplayProvider.from_file(scenario_dir / "provider.jsonl")   # strict: a 6th request is a failure
lenient = ScriptedProvider([[TextDelta(text="hi"), Done(stop_reason="end_turn")]])   # polite by default
strict = ScriptedProvider([...], strict_exhaustion=True)               # same strictness, hand-written script

# To make a fixture: wrap the real provider, run the flow, flush.
tee = RecordingProvider(real_provider, out_dir)      # refuses to write a stream containing $*_API_KEY
```

`provider.jsonl` is one JSON object per `ModelEvent` (tagged `"t"`), a blank line between requests — readable
in a diff and appendable by hand. See [`testing/SCENARIOS.md`](testing/SCENARIOS.md) for the end-to-end loop.

### Running Doctor Diagnostics
```bash
# Inspect a single plugin
harness plugin doctor ./my_rules

# Diagnose all discovered plugins
harness plugin doctor --all
```
Doctor validates:
- Manifest structure and schema compatibility.
- AST import boundaries (ensuring only `harness.sdk` is imported).
- Subprocess isolation (prohibiting raw `subprocess` imports).
- Capability declarations (verifying matching usage of secrets, exec).
- Deprecated legacy module usages.

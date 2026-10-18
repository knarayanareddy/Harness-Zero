"""
Plugin Scaffolder for Harness-Zero SDK.
Generates template plugins per component kind with manifests, schemas,
conformance tests, and READMEs (§11, SDK-01).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from harness.sdk.manifest import VALID_PROVIDES
from harness.sdk.types import SDK_API_VERSION


def _to_class_name(name: str) -> str:
    """Converts kebab-case or snake_case name to CamelCase."""
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", name)
    return "".join(part.capitalize() for part in clean.split())


KIND_CAPABILITIES: dict[str, list[str]] = {
    "tool": ["fs:read"],
    "context": [],
    "provider": ["net"],
    "policy": ["approval"],
    "executor": ["exec"],
    "strategy": [],
    "hook": [],
    "store": ["fs:read", "fs:write"],
    "bridge": ["net"],
}

KIND_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "context": (
        """\"\"\"
{name} Context Source Plugin.
\"\"\"
from __future__ import annotations

from typing import Any

from harness.sdk.context import PluginContext
from harness.sdk.contracts import ContextSource
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import ContextBlock, Stability


class {class_name}ContextSource(ContextSource):
    name: str = "{name}"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        \"\"\"Returns context blocks for prompt assembly.\"\"\"
        return [
            ContextBlock(
                id="{name}:rules",
                stability=Stability.SESSION,
                text="Active rules from {name} context plugin.",
            )
        ]


class {class_name}Plugin:
    \"\"\"Plugin lifecycle registration.\"\"\"

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_context_source({class_name}ContextSource())

    async def teardown(self) -> None:
        pass
""",
        """import pytest
from harness.sdk.testing import ContextKit
from plugin import {class_name}ContextSource


@pytest.mark.asyncio
async def test_conformance():
    kit = ContextKit()
    source = {class_name}ContextSource()
    await kit.test_block_ids_unique(source, ctx=None)
    await kit.test_static_blocks_byte_identical(source, ctx=None)
""",
        "ContextSource",
    ),
    "tool": (
        """\"\"\"
{name} Tool Plugin.
\"\"\"
from __future__ import annotations

from typing import Any

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import RiskClass, ToolResult, ToolSpec


class {class_name}Tool:
    spec: ToolSpec = ToolSpec(
        name="{name}",
        description="Executes {name} action",
        input_schema={{
            "type": "object",
            "properties": {{
                "input": {{"type": "string", "description": "Input parameter"}}
            }},
            "required": ["input"],
            "additionalProperties": False,
        }},
        risk_class=RiskClass.READ,
    )

    async def __call__(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        inp = args.get("input", "")
        return ToolResult(
            call_id=getattr(ctx, "call_id", "call_1"),
            name="{name}",
            output=f"Executed {name} successfully: {{inp}}",
        )


class {class_name}Plugin:
    \"\"\"Plugin lifecycle registration.\"\"\"

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        tool = {class_name}Tool()
        ctx.register_tool(tool.spec, tool)

    async def teardown(self) -> None:
        pass
""",
        """from harness.sdk.testing import ToolKit
from plugin import {class_name}Tool


def test_conformance():
    kit = ToolKit()
    tool = {class_name}Tool()
    kit.test_input_schema_valid_and_closed(tool.spec)
    kit.test_declared_risk_class_bound(tool.spec)
    kit.test_no_direct_subprocess_import({class_name}Tool)
""",
        "ToolHandler",
    ),
    "policy": (
        """\"\"\"
{name} Policy Plugin.
\"\"\"
from __future__ import annotations

from typing import Any

from harness.sdk.context import PluginContext
from harness.sdk.contracts import Policy
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import Action, Decision, ToolCall, ToolSpec


class {class_name}Policy(Policy):
    name: str = "{name}"
    order: int = 500

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: Any) -> Decision:
        return Decision(
            action=Action.ALLOW,
            reason=f"Allowed by {{self.name}}",
            policy=self.name,
        )


class {class_name}Plugin:
    \"\"\"Plugin lifecycle registration.\"\"\"

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_policy({class_name}Policy())

    async def teardown(self) -> None:
        pass
""",
        """import pytest
from harness.sdk.testing import PolicyKit
from plugin import {class_name}Policy


@pytest.mark.asyncio
async def test_conformance():
    kit = PolicyKit()
    policy = {class_name}Policy()
    kit.test_policy_order_range(policy)
    await kit.test_returns_decision_instance(policy, ctx=None)
    await kit.test_deterministic_evaluation(policy, ctx=None)
""",
        "Policy",
    ),
    "provider": (
        """\"\"\"
{name} Provider Plugin.
\"\"\"
from __future__ import annotations

from collections.abc import AsyncIterator

from harness.sdk.context import PluginContext
from harness.sdk.contracts import Provider
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import (
    Done,
    ModelEvent,
    ModelInfo,
    ModelRequest,
    ProviderCaps,
    TextDelta,
    Usage,
    UsageEvent,
)


class {class_name}Provider(Provider):
    name: str = "{name}"
    caps: ProviderCaps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=False,
        cache_hints=False,
        thinking=False,
    )

    def is_configured(self) -> bool:
        # Return False when a required credential is missing; the kernel then lists your models as "needs key".
        return True

    async def is_reachable(self) -> bool | None:
        # Probe a local endpoint with a short timeout (cache ~30 s) or return None for hosted APIs.
        return None

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="{name}-default",
                display_name="{class_name} Default Model",
                context_tokens=128000,
            )
        ]

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="Response chunk 1 from {name}. ")
        yield TextDelta(text="Response chunk 2 from {name}.")
        yield Done(stop_reason="end_turn")
        yield UsageEvent(usage=Usage(input_tokens=10, output_tokens=5))

    async def aclose(self) -> None:
        pass


class {class_name}Plugin:
    \"\"\"Plugin lifecycle registration.\"\"\"

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_provider({class_name}Provider())

    async def teardown(self) -> None:
        pass
""",
        """import pytest
from harness.sdk.testing import ProviderKit
from plugin import {class_name}Provider


@pytest.mark.asyncio
async def test_conformance():
    kit = ProviderKit()
    provider = {class_name}Provider()
    await kit.test_readiness(provider)
    await kit.test_list_models(provider)
    await kit.test_plain_prompt(provider)
    await kit.test_streaming_multiple_deltas(provider)
    await kit.test_aclose_idempotent(provider)
""",
        "Provider",
    ),
    "hook": (
        """\"\"\"
{name} Hook Plugin.
\"\"\"
from __future__ import annotations

from typing import Any

from harness.sdk.context import PluginContext
from harness.sdk.contracts import Hook
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import HookEvent, HookOutcome


class {class_name}Hook(Hook):
    name: str = "{name}"
    events: frozenset[HookEvent] = frozenset([HookEvent.PRE_TOOL_USE])
    matcher: str | None = None
    timeout_s: float = 5.0

    async def on(
        self, event: HookEvent, payload: dict[str, Any], ctx: Any
    ) -> HookOutcome:
        return HookOutcome(action="continue", reason=f"Allowed by {{self.name}}")


class {class_name}Plugin:
    \"\"\"Plugin lifecycle registration.\"\"\"

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_hook({class_name}Hook())

    async def teardown(self) -> None:
        pass
""",
        """import pytest
from harness.sdk.testing import HookKit
from plugin import {class_name}Hook


@pytest.mark.asyncio
async def test_conformance():
    kit = HookKit()
    hook = {class_name}Hook()
    await kit.test_hook_outcome_action(hook, ctx=None)
""",
        "Hook",
    ),
    "executor": (
        """\"\"\"
{name} Executor Plugin.
\"\"\"
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from harness.sdk.context import PluginContext
from harness.sdk.contracts import Executor, Process
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import ExecRequest, ExecResult


class {class_name}Process(Process):
    def __init__(self, proc_id: str, command: list[str]) -> None:
        self.id = proc_id
        self.pid = 1234
        self.command = command
        self._output_chunks: list[tuple[Literal["stdout", "stderr"], str]] = [
            ("stdout", f"Output for: {{' '.join(command)}}\\n")
        ]

    async def output(self) -> AsyncIterator[tuple[Literal["stdout", "stderr"], str]]:
        for stream, chunk in self._output_chunks:
            yield stream, chunk

    async def send_input(self, text: str) -> None:
        pass

    async def kill(self, grace_s: float = 2.0) -> None:
        pass

    async def wait(self) -> ExecResult:
        token = self.command[1] if len(self.command) > 1 else "ok"
        return ExecResult(exit_code=0, stdout=f"{{token}}\\n", stderr="", truncated=False, duration_ms=10)


class {class_name}Executor(Executor):
    name: str = "{name}"
    isolation: Literal["none", "worktree", "container", "vm"] = "none"

    async def start(self, req: ExecRequest) -> Process:
        return {class_name}Process(proc_id="proc_1", command=req.command)


class {class_name}Plugin:
    \"\"\"Plugin lifecycle registration.\"\"\"

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_executor({class_name}Executor())

    async def teardown(self) -> None:
        pass
""",
        """from pathlib import Path
import pytest
from harness.sdk.testing import ExecutorKit
from plugin import {class_name}Executor


@pytest.mark.asyncio
async def test_conformance(tmp_path: Path):
    kit = ExecutorKit()
    executor = {class_name}Executor()
    await kit.test_echo_round_trip(executor, tmp_path)
""",
        "Executor",
    ),
    "strategy": (
        """\"\"\"
{name} Strategy Plugin.
\"\"\"
from __future__ import annotations

from typing import Any

from harness.sdk.context import PluginContext
from harness.sdk.contracts import Strategy
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import TurnResult


class {class_name}Strategy(Strategy):
    name: str = "{name}"

    async def run(self, turn: Any, k: Any) -> TurnResult:
        return TurnResult(
            output="Completed by {name} strategy",
            stop_reason="end_turn",
        )


class {class_name}Plugin:
    \"\"\"Plugin lifecycle registration.\"\"\"

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_strategy({class_name}Strategy())

    async def teardown(self) -> None:
        pass
""",
        """from types import SimpleNamespace
import pytest
from harness.sdk.testing import StrategyKit
from plugin import {class_name}Strategy


@pytest.mark.asyncio
async def test_conformance():
    kit = StrategyKit()
    strategy = {class_name}Strategy()
    turn_ctx = SimpleNamespace(turn_id="turn_1")
    await kit.test_strategy_completes_turn(strategy, turn_ctx, None)
""",
        "Strategy",
    ),
    "store": (
        """\"\"\"
{name} Store Plugin.
\"\"\"
from __future__ import annotations

from typing import Any

from harness.sdk.context import PluginContext
from harness.sdk.contracts import TranscriptStore
from harness.sdk.manifest import PluginManifest


class {class_name}TranscriptStore(TranscriptStore):
    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    async def append(self, record: dict[str, Any]) -> int:
        idx = len(self._records) + 1
        rec = dict(record)
        rec["step_index"] = idx
        self._records.append(rec)
        return idx

    async def read(self, since_step: int = 0, compact: bool = True) -> list[dict[str, Any]]:
        return [r for r in self._records if r.get("step_index", 0) >= since_step]

    async def last_compaction(self) -> dict[str, Any] | None:
        return None


class {class_name}Plugin:
    \"\"\"Plugin lifecycle registration.\"\"\"

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_store("transcript", {class_name}TranscriptStore())

    async def teardown(self) -> None:
        pass
""",
        """import pytest
from harness.sdk.testing import StoreKit
from plugin import {class_name}TranscriptStore


@pytest.mark.asyncio
async def test_conformance():
    kit = StoreKit()
    store = {class_name}TranscriptStore()
    await kit.test_append_read_round_trip(store)
""",
        "TranscriptStore",
    ),
}


def scaffold_plugin(
    name: str,
    kind: str,
    target_dir: Path | str | None = None,
    summary: str = "",
) -> Path:
    """
    Scaffolds a new Harness-Zero plugin directory.
    Creates:
    - harness_plugin.toml
    - settings.schema.json
    - plugin.py
    - README.md
    - tests/__init__.py
    - tests/test_conformance.py
    """
    name_clean = name.strip().lower().replace("_", "-")
    if not re.match(r"^[a-z][a-z0-9-]{1,40}$", name_clean):
        raise ValueError(
            f"Invalid plugin name '{name}': must match regex ^[a-z][a-z0-9-]{{1,40}}$"
        )

    kind_clean = kind.strip().lower()
    if kind_clean not in VALID_PROVIDES:
        raise ValueError(
            f"Invalid plugin kind '{kind}'. Must be one of: {sorted(VALID_PROVIDES)}"
        )

    if kind_clean not in KIND_TEMPLATES:
        raise ValueError(
            f"Scaffolding not supported for kind '{kind_clean}'. Supported kinds: {sorted(KIND_TEMPLATES.keys())}"
        )

    out_dir = Path(target_dir).resolve() if target_dir else Path.cwd() / name_clean
    out_dir.mkdir(parents=True, exist_ok=True)

    class_name = _to_class_name(name_clean)
    plugin_summary = summary.strip() or f"{name_clean} {kind_clean} plugin for Harness-Zero"
    capabilities = KIND_CAPABILITIES.get(kind_clean, [])

    # 1. harness_plugin.toml
    caps_str = json.dumps(capabilities)
    toml_content = f"""[plugin]
name = "{name_clean}"
version = "0.1.0"
api_version = "{SDK_API_VERSION}"
summary = "{plugin_summary}"
provides = ["{kind_clean}"]
capabilities = {caps_str}
entry = "plugin:{class_name}Plugin"

[settings]
schema = "settings.schema.json"
"""
    (out_dir / "harness_plugin.toml").write_text(toml_content, encoding="utf-8")

    # 2. settings.schema.json
    schema_content = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "title": f"{class_name}Settings",
        "description": f"Configuration settings for {name_clean} plugin",
        "properties": {},
        "additionalProperties": False,
    }
    (out_dir / "settings.schema.json").write_text(
        json.dumps(schema_content, indent=2) + "\n", encoding="utf-8"
    )

    # 3. plugin.py & tests/test_conformance.py
    py_tmpl, test_tmpl, _ = KIND_TEMPLATES[kind_clean]
    py_code = py_tmpl.format(name=name_clean, class_name=class_name)
    test_code = test_tmpl.format(name=name_clean, class_name=class_name)

    (out_dir / "plugin.py").write_text(py_code, encoding="utf-8")

    tests_dir = out_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_conformance.py").write_text(test_code, encoding="utf-8")

    # 4. README.md
    readme_content = f"""# {name_clean}

{plugin_summary}

## Kind
`{kind_clean}`

## Capabilities
`{capabilities}`

## Configuration
Settings are schema-validated using `settings.schema.json`.

## Testing
Run plugin conformance tests:
```bash
harness plugin test .
```
"""
    (out_dir / "README.md").write_text(readme_content, encoding="utf-8")

    return out_dir

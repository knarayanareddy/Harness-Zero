"""
Conformance Test Kits for Harness-Zero SDK plugins.
Exposes modular base test classes for Providers, Tools, Policies,
Executors, ContextSources, Stores, Strategies, Hooks, and Frontends (§11).
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harness.sdk.cards import Card
from harness.sdk.contracts import (
    ContextSource,
    Executor,
    Hook,
    Policy,
    Provider,
    Strategy,
    TranscriptStore,
)
from harness.sdk.errors import ProviderError
from harness.sdk.types import (
    ContextBlock,
    Decision,
    Done,
    ExecRequest,
    HookOutcome,
    Message,
    ModelParams,
    ModelRequest,
    RiskClass,
    Role,
    Stability,
    TextBlock,
    TextDelta,
    ToolCall,
    ToolSpec,
    TurnResult,
    UsageEvent,
)


class ProviderKit:
    """Conformance test kit for Provider plugins."""

    async def test_list_models(self, provider: Provider) -> None:
        """Provider must list >= 1 supported model or raise ProviderError(kind='auth')."""
        try:
            models = await provider.list_models()
            assert len(models) >= 1, f"Provider '{provider.name}' returned empty model list"
            for m in models:
                assert m.id, "ModelInfo missing id"
                assert m.display_name, "ModelInfo missing display_name"
        except ProviderError as err:
            assert err.kind == "auth", f"Expected auth ProviderError, got: {err.kind}"

    async def test_plain_prompt(self, provider: Provider) -> None:
        """Provider must yield >= 1 TextDelta, exactly one Done, and UsageEvent."""
        req = ModelRequest(
            model=(await provider.list_models())[0].id,
            system=[ContextBlock(id="sys", text="Be concise.", stability=Stability.STATIC)],
            messages=[Message(role=Role.USER, content=[TextBlock(text="Respond with 'HELLO'")])],
            tools=[],
            params=ModelParams(),
        )

        deltas = 0
        done_count = 0
        usage_count = 0

        async for event in provider.generate(req):
            if isinstance(event, TextDelta):
                deltas += 1
            elif isinstance(event, Done):
                done_count += 1
            elif isinstance(event, UsageEvent):
                usage_count += 1

        assert deltas >= 1, "Provider did not yield any TextDelta events"
        assert done_count == 1, f"Expected exactly 1 Done event, got {done_count}"
        assert usage_count >= 1, "Provider did not yield any UsageEvent"

    async def test_streaming_multiple_deltas(self, provider: Provider) -> None:
        """When streaming is enabled, provider should emit >= 2 deltas."""
        if not provider.caps.streaming:
            return

        req = ModelRequest(
            model=(await provider.list_models())[0].id,
            system=[],
            messages=[Message(role=Role.USER, content=[TextBlock(text="Count 1 to 5")])],
            tools=[],
            params=ModelParams(),
        )

        deltas = 0
        async for event in provider.generate(req):
            if isinstance(event, TextDelta):
                deltas += 1

        assert deltas >= 2, f"Streaming provider emitted only {deltas} deltas"

    async def test_aclose_idempotent(self, provider: Provider) -> None:
        """aclose() must be safe and idempotent."""
        await provider.aclose()
        await provider.aclose()


class ToolKit:
    """Conformance test kit for Tool plugins."""

    def test_input_schema_valid_and_closed(self, spec: ToolSpec) -> None:
        """Tool input_schema must be valid JSON Schema with additionalProperties: false."""
        assert isinstance(spec.input_schema, dict), "input_schema must be a dict"
        assert spec.input_schema.get("type") == "object", "input_schema type must be 'object'"
        assert spec.input_schema.get("additionalProperties") is False, (
            f"Tool '{spec.name}' input_schema must declare additionalProperties: false"
        )

    def test_workspace_path_formatting(self, spec: ToolSpec) -> None:
        """Every path-like argument must declare format: 'workspace-path' (§5.3)."""
        props = spec.input_schema.get("properties", {})
        for name, sub in props.items():
            if any(term in name.lower() for term in ("path", "file", "directory", "dest", "src")):
                fmt = sub.get("format")
                assert fmt == "workspace-path", (
                    f"Path argument '{name}' in tool '{spec.name}' must declare format: 'workspace-path'"
                )

    def test_declared_risk_class_bound(self, spec: ToolSpec) -> None:
        """Tool must declare an allowed RiskClass."""
        allowed = {
            RiskClass.READ,
            RiskClass.WRITE,
            RiskClass.EXEC,
            RiskClass.NETWORK,
            RiskClass.DESTRUCTIVE,
        }
        assert spec.risk_class in allowed, f"Invalid risk_class: {spec.risk_class}"

    def test_no_direct_subprocess_import(self, tool_func_or_module: Any) -> None:
        """Tool implementation must not import subprocess directly; must use ctx.executor (§9.4)."""
        src = textwrap.dedent(inspect.getsource(tool_func_or_module))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "subprocess", "Direct subprocess import forbidden in tools"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "subprocess", "Direct subprocess import forbidden in tools"


class PolicyKit:
    """Conformance test kit for Policy plugins."""

    def test_policy_order_range(self, policy: Policy) -> None:
        """Policy order must be within 0..1000 (§5.3)."""
        assert 0 <= policy.order <= 1000, f"Policy order {policy.order} out of range [0, 1000]"

    async def test_returns_decision_instance(self, policy: Policy, ctx: Any) -> None:
        """evaluate() must return a Decision instance and never raise."""
        dummy_spec = ToolSpec(
            name="test_tool",
            description="test",
            input_schema={"type": "object", "additionalProperties": False},
            risk_class=RiskClass.READ,
        )
        dummy_call = ToolCall(id="call_test", name="test_tool", arguments={})
        decision = await policy.evaluate(dummy_call, dummy_spec, ctx)
        assert isinstance(decision, Decision), f"Expected Decision, got {type(decision)}"
        assert decision.action in ("allow", "ask", "deny")

    async def test_deterministic_evaluation(self, policy: Policy, ctx: Any) -> None:
        """Policy must be deterministic for identical input."""
        dummy_spec = ToolSpec(
            name="test_tool",
            description="test",
            input_schema={"type": "object", "additionalProperties": False},
            risk_class=RiskClass.READ,
        )
        dummy_call = ToolCall(id="call_test", name="test_tool", arguments={})
        d1 = await policy.evaluate(dummy_call, dummy_spec, ctx)
        d2 = await policy.evaluate(dummy_call, dummy_spec, ctx)
        assert d1.action == d2.action
        assert d1.reason == d2.reason


class ExecutorKit:
    """Conformance test kit for Executor plugins."""

    async def test_echo_round_trip(self, executor: Executor, tmp_path: Path) -> None:
        """Executor must run command and return output."""
        req = ExecRequest(
            command=["echo", "conformance_echo_token_42"],
            cwd=tmp_path,
        )
        proc = await executor.start(req)
        res = await proc.wait()
        assert res.exit_code == 0
        assert "conformance_echo_token_42" in res.stdout

    async def test_cwd_enforced(self, executor: Executor, tmp_path: Path) -> None:
        """Executor must enforce specified working directory."""
        subdir = tmp_path / "sub_test"
        subdir.mkdir(parents=True, exist_ok=True)
        req = ExecRequest(command=["pwd"], cwd=subdir)
        proc = await executor.start(req)
        res = await proc.wait()
        assert res.exit_code == 0
        assert str(subdir) in res.stdout

    async def test_kill_idempotency(self, executor: Executor, tmp_path: Path) -> None:
        """Process kill() must be idempotent."""
        req = ExecRequest(command=["sleep", "10"], cwd=tmp_path)
        proc = await executor.start(req)
        await proc.kill(grace_s=0.5)
        await proc.kill(grace_s=0.5)
        res = await proc.wait()
        assert res.exit_code != 0


class ContextKit:
    """Conformance test kit for ContextSource plugins."""

    async def test_static_blocks_byte_identical(self, source: ContextSource, ctx: Any) -> None:
        """STATIC blocks must be byte-for-byte identical across consecutive calls (§5.4)."""
        b1 = await source.blocks(ctx)
        b2 = await source.blocks(ctx)
        static1 = [b for b in b1 if b.stability == Stability.STATIC]
        static2 = [b for b in b2 if b.stability == Stability.STATIC]

        assert len(static1) == len(static2), "Number of STATIC blocks changed across calls"
        for x, y in zip(static1, static2, strict=False):
            assert x.id == y.id, f"STATIC block id changed: {x.id} vs {y.id}"
            assert x.text == y.text, f"STATIC block text mutated for block '{x.id}'"

    async def test_block_ids_unique(self, source: ContextSource, ctx: Any) -> None:
        """All emitted context block IDs must be unique."""
        blocks = await source.blocks(ctx)
        ids = [b.id for b in blocks]
        assert len(ids) == len(set(ids)), f"Duplicate context block ids detected: {ids}"


class StoreKit:
    """Conformance test kit for TranscriptStore plugins."""

    async def test_append_read_round_trip(self, store: TranscriptStore) -> None:
        """Appended record must be read back accurately."""
        rec = {"type": "USER", "content": "Conformance store test prompt", "timestamp": 123456.0}
        idx = await store.append(rec)
        assert idx >= 1

        records = await store.read(since_step=idx)
        assert any(r.get("content") == "Conformance store test prompt" for r in records)


class StrategyKit:
    """Conformance test kit for Strategy plugins."""

    async def test_strategy_completes_turn(
        self, strategy: Strategy, turn_ctx: Any, kernel_services: Any
    ) -> None:
        """Strategy must run through kernel services and return TurnResult."""
        res = await strategy.run(turn_ctx, kernel_services)
        assert isinstance(res, TurnResult), f"Expected TurnResult, got {type(res)}"
        assert res.stop_reason in ("end_turn", "tool_use", "cancelled", "max_tokens")


class HookKit:
    """Conformance test kit for Hook plugins."""

    async def test_hook_outcome_action(self, hook: Hook, ctx: Any) -> None:
        """Hook must return HookOutcome with valid action ('continue', 'block', 'mutate')."""
        if not hook.events:
            return
        event = next(iter(hook.events))
        outcome = await hook.on(event, {}, ctx)
        assert isinstance(outcome, HookOutcome)
        assert outcome.action in ("continue", "block", "mutate")


class FrontendKit:
    """Conformance test kit for Frontend card rendering and fallback (§7)."""

    def test_renders_all_builtin_card_kinds(
        self, render_card_fn: Callable[[Card], Any]
    ) -> None:
        """Frontend must render every built-in card kind without crashing."""
        card_fixtures: list[Card] = [
            Card.markdown("### Heading\nText"),
            Card.code("print('hello')", language="python"),
            Card.diff("file.py", "+line", added=1, removed=0),
            Card.table(["Col A", "Col B"], [["1", "2"]]),
            Card.notice("System info", level="info"),
            Card.progress("Uploading", 0.5),
            Card.tree([{"label": "root", "children": []}]),
            Card.approval("app_1", "Approve action", details={}),
            Card.artifact("art.txt", "text/plain", "/art.txt"),
            Card.form("form_1", {"type": "object"}, "Submit"),
        ]
        for card in card_fixtures:
            res = render_card_fn(card)
            assert res is not None

    def test_renders_unknown_card_fallback(
        self, render_card_fn: Callable[[Card], Any]
    ) -> None:
        """Unknown card kind must render fallback without throwing (§7)."""
        unknown_card = Card(
            kind="x-custom-widget",
            id="card_custom_01",
            title="Custom Widget",
            data={"arbitrary": 42},
        )
        res = render_card_fn(unknown_card)
        assert res is not None

"""
Meta-tests for KRN-10:
Conformance kits (ProviderKit, ToolKit, PolicyKit, ContextKit, FrontendKit, ScriptedProvider).
Verifies that conformance kits pass valid implementations and deliberately fail broken ones with clear messages (§11).
"""
from __future__ import annotations

import pytest

from harness.sdk.cards import Card, fallback
from harness.sdk.contracts import ContextSource, Policy
from harness.sdk.testing import (
    ContextKit,
    FrontendKit,
    PolicyKit,
    ProviderKit,
    ScriptedProvider,
    ToolKit,
)
from harness.sdk.types import (
    ContextBlock,
    Decision,
    Done,
    Message,
    ModelParams,
    ModelRequest,
    RiskClass,
    Role,
    Stability,
    TextBlock,
    TextDelta,
    ToolSpec,
    Usage,
    UsageEvent,
)

# =========================================================================
# 1. ProviderKit Meta-Tests
# =========================================================================

@pytest.mark.asyncio
async def test_provider_kit_passes_valid_provider():
    """ProviderKit passes on a conforming ScriptedProvider."""
    kit = ProviderKit()
    provider = ScriptedProvider(
        responses=[
            [TextDelta(text="Chunk 1"), TextDelta(text="Chunk 2"), Done(stop_reason="end_turn"), UsageEvent(usage=Usage())],
            [TextDelta(text="Chunk 1"), TextDelta(text="Chunk 2"), Done(stop_reason="end_turn"), UsageEvent(usage=Usage())],
        ]
    )
    await kit.test_list_models(provider)
    await kit.test_plain_prompt(provider)
    await kit.test_streaming_multiple_deltas(provider)
    await kit.test_aclose_idempotent(provider)


@pytest.mark.asyncio
async def test_provider_kit_fails_empty_models():
    """ProviderKit catches provider returning empty model list."""
    kit = ProviderKit()
    broken_provider = ScriptedProvider(models=[])
    with pytest.raises(AssertionError) as exc_info:
        await kit.test_list_models(broken_provider)
    assert "returned empty model list" in str(exc_info.value)


@pytest.mark.asyncio
async def test_provider_kit_fails_missing_deltas():
    """ProviderKit catches provider that finishes without yielding TextDelta."""
    kit = ProviderKit()
    broken_provider = ScriptedProvider(responses=[[Done(stop_reason="end_turn"), UsageEvent(usage=Usage())]])
    with pytest.raises(AssertionError) as exc_info:
        await kit.test_plain_prompt(broken_provider)
    assert "did not yield any TextDelta" in str(exc_info.value)


@pytest.mark.asyncio
async def test_provider_kit_fails_missing_done():
    """ProviderKit catches provider that does not emit Done event."""
    kit = ProviderKit()
    broken_provider = ScriptedProvider(responses=[[TextDelta(text="Some output"), UsageEvent(usage=Usage())]])
    with pytest.raises(AssertionError) as exc_info:
        await kit.test_plain_prompt(broken_provider)
    assert "Expected exactly 1 Done event" in str(exc_info.value)


# =========================================================================
# 2. ToolKit Meta-Tests
# =========================================================================

def test_tool_kit_passes_valid_tool():
    """ToolKit passes on a conforming ToolSpec."""
    kit = ToolKit()
    valid_spec = ToolSpec(
        name="fs_read",
        description="Reads file",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "format": "workspace-path"},
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
        risk_class=RiskClass.READ,
    )
    kit.test_input_schema_valid_and_closed(valid_spec)
    kit.test_workspace_path_formatting(valid_spec)
    kit.test_declared_risk_class_bound(valid_spec)


def test_tool_kit_fails_open_schema():
    """ToolKit rejects tool schema without additionalProperties: false."""
    kit = ToolKit()
    broken_spec = ToolSpec(
        name="bad_tool",
        description="bad",
        input_schema={
            "type": "object",
            "properties": {"arg": {"type": "string"}},
            "additionalProperties": True,  # Non-compliant!
        },
        risk_class=RiskClass.READ,
    )
    with pytest.raises(AssertionError) as exc_info:
        kit.test_input_schema_valid_and_closed(broken_spec)
    assert "additionalProperties: false" in str(exc_info.value)


def test_tool_kit_fails_unformatted_path():
    """ToolKit rejects tool schema where path-like argument lacks format: 'workspace-path'."""
    kit = ToolKit()
    broken_spec = ToolSpec(
        name="bad_path_tool",
        description="bad",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},  # Missing format: workspace-path!
            },
            "additionalProperties": False,
        },
        risk_class=RiskClass.READ,
    )
    with pytest.raises(AssertionError) as exc_info:
        kit.test_workspace_path_formatting(broken_spec)
    assert "workspace-path" in str(exc_info.value)


def test_tool_kit_detects_subprocess_import():
    """ToolKit detects direct subprocess imports in tool implementations."""
    kit = ToolKit()

    def bad_tool_handler():
        import subprocess  # Non-compliant!
        return subprocess.run(["ls"])

    with pytest.raises(AssertionError) as exc_info:
        kit.test_no_direct_subprocess_import(bad_tool_handler)
    assert "Direct subprocess import forbidden in tools" in str(exc_info.value)


# =========================================================================
# 3. PolicyKit Meta-Tests
# =========================================================================

@pytest.mark.asyncio
async def test_policy_kit_passes_valid_policy():
    """PolicyKit passes a conforming Policy."""
    kit = PolicyKit()

    class ConformingPolicy(Policy):
        name: str = "conforming_policy"
        order: int = 250

        async def evaluate(self, call, spec, ctx):
            return Decision.allow("All checks passed")

    p = ConformingPolicy()
    kit.test_policy_order_range(p)
    await kit.test_returns_decision_instance(p, ctx=None)
    await kit.test_deterministic_evaluation(p, ctx=None)


def test_policy_kit_fails_out_of_range_order():
    """PolicyKit rejects policy with order outside 0..1000."""
    kit = PolicyKit()

    class BadOrderPolicy(Policy):
        name: str = "bad_order"
        order: int = 1500  # Out of range!

        async def evaluate(self, call, spec, ctx):
            return Decision.allow()

    with pytest.raises(AssertionError) as exc_info:
        kit.test_policy_order_range(BadOrderPolicy())
    assert "out of range" in str(exc_info.value)


# =========================================================================
# 4. ContextKit Meta-Tests
# =========================================================================

@pytest.mark.asyncio
async def test_context_kit_passes_valid_source():
    """ContextKit passes a deterministic context source."""
    kit = ContextKit()

    class ConformingSource(ContextSource):
        name: str = "identity_source"

        async def blocks(self, ctx):
            return [
                ContextBlock(id="static_ident", text="Static identity", stability=Stability.STATIC),
                ContextBlock(id="sess_notes", text="Session notes", stability=Stability.SESSION),
            ]

    src = ConformingSource()
    await kit.test_static_blocks_byte_identical(src, ctx=None)
    await kit.test_block_ids_unique(src, ctx=None)


@pytest.mark.asyncio
async def test_context_kit_fails_mutating_static_block():
    """ContextKit catches a source whose STATIC block mutates across calls."""
    kit = ContextKit()
    call_count = 0

    class MutatingSource(ContextSource):
        name: str = "mutating_source"

        async def blocks(self, ctx):
            nonlocal call_count
            call_count += 1
            return [
                ContextBlock(
                    id="static_block",
                    text=f"Static text counter={call_count}",  # Mutates!
                    stability=Stability.STATIC,
                )
            ]

    src = MutatingSource()
    with pytest.raises(AssertionError) as exc_info:
        await kit.test_static_blocks_byte_identical(src, ctx=None)
    assert "STATIC block text mutated" in str(exc_info.value)


@pytest.mark.asyncio
async def test_context_kit_fails_duplicate_block_ids():
    """ContextKit catches duplicate block IDs emitted by a source."""
    kit = ContextKit()

    class DuplicateIdSource(ContextSource):
        name: str = "duplicate_source"

        async def blocks(self, ctx):
            return [
                ContextBlock(id="dup_id", text="Block 1", stability=Stability.STATIC),
                ContextBlock(id="dup_id", text="Block 2", stability=Stability.STATIC),
            ]

    src = DuplicateIdSource()
    with pytest.raises(AssertionError) as exc_info:
        await kit.test_block_ids_unique(src, ctx=None)
    assert "Duplicate context block ids detected" in str(exc_info.value)


# =========================================================================
# 5. FrontendKit Meta-Tests
# =========================================================================

def test_frontend_kit_verifies_rendering_and_fallback():
    """FrontendKit tests built-in card kinds and unknown card fallback rendering."""
    kit = FrontendKit()

    def dummy_renderer(card: Card) -> str:
        # Uses standard SDK fallback for unknown cards
        if card.kind in ("markdown", "code", "diff", "table", "notice", "progress", "tree", "approval", "artifact", "form"):
            return f"Rendered:{card.kind}"
        fb = fallback(card)
        return fb.data.get("body", "Fallback")

    kit.test_renders_all_builtin_card_kinds(dummy_renderer)
    kit.test_renders_unknown_card_fallback(dummy_renderer)


# =========================================================================
# 6. ScriptedProvider Request Assertions
# =========================================================================

@pytest.mark.asyncio
async def test_scripted_provider_assertions():
    """Verifies that ScriptedProvider recorded request assertions function correctly."""
    p = ScriptedProvider(responses=[[Done(stop_reason="end_turn")]])

    req = ModelRequest(
        model="scripted-v1",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Find the answer")])],
        tools=[],
        params=ModelParams(),
    )

    async for _ in p.generate(req):
        pass

    # Passing assertions
    p.assert_requested(count=1)
    p.assert_requested(model="scripted-v1")
    p.assert_requested(contains_text="Find the answer")

    # Failing assertions
    with pytest.raises(AssertionError):
        p.assert_requested(count=5)

    with pytest.raises(AssertionError):
        p.assert_requested(model="nonexistent-model")

    with pytest.raises(AssertionError):
        p.assert_requested(contains_text="text that was never sent")

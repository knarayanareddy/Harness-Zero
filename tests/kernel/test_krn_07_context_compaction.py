"""
Tests for KRN-07: ContextAssembler and Compaction (§5.4, §5.5).
Absorbs LOOP-02 and CONF-04.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.compaction import (
    COMPACTION_SYSTEM_PROMPT,
    Compactor,
    estimate_tokens,
)
from harness.kernel.context import ContextAssembler, records_to_messages
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.sdk.contracts import ContextSource, Hook
from harness.sdk.types import (
    ContextBlock,
    HookEvent,
    HookOutcome,
    ImageBlock,
    Message,
    Role,
    Stability,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class DummyStaticSource(ContextSource):
    name = "identity_instructions"

    def __init__(self, text: str):
        self.text = text

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        return [
            ContextBlock(
                id="static_identity",
                text=self.text,
                stability=Stability.STATIC,
                priority=10,
                source=self.name,
            )
        ]


class DummySessionSource(ContextSource):
    name = "skills_catalog"

    def __init__(self, text: str):
        self.text = text

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        return [
            ContextBlock(
                id="session_skills",
                text=self.text,
                stability=Stability.SESSION,
                priority=50,
                source=self.name,
            )
        ]


class FailingSource(ContextSource):
    name = "broken_source"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        raise RuntimeError("Database connection timed out during context fetch")


class HangingSource(ContextSource):
    name = "hanging_source"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        await asyncio.sleep(10.0)
        return []


@pytest.fixture
def assembler_setup(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings.json")
    secrets = Secrets(tmp_path / "secrets.json")
    registry = PluginRegistry(
        workspace=workspace,
        settings=settings,
        secrets=secrets,
        bus=bus,
    )
    assembler = ContextAssembler(
        registry=registry,
        settings=settings,
        bus=bus,
        timeout_s=0.2,  # Fast timeout for tests
    )
    compactor = Compactor(
        settings=settings,
        bus=bus,
        registry=registry,
    )
    return {
        "workspace": workspace,
        "bus": bus,
        "settings": settings,
        "secrets": secrets,
        "registry": registry,
        "assembler": assembler,
        "compactor": compactor,
    }


@pytest.mark.asyncio
async def test_invariance_across_two_assemblies(assembler_setup):
    """STATIC blocks must be byte-identical and SESSION blocks stable across multiple calls."""
    registry: PluginRegistry = assembler_setup["registry"]
    assembler: ContextAssembler = assembler_setup["assembler"]

    src_static = DummyStaticSource("You are Antigravity, an AI assistant.")
    src_session = DummySessionSource("Skills: bash, python, git.")

    registry._register_context_source(src_static, "builtin")
    registry._register_context_source(src_session, "builtin")

    req1 = await assembler.assemble(
        session_id="session_01",
        user_prompt="Hello world",
    )
    req2 = await assembler.assemble(
        session_id="session_01",
        user_prompt="Hello world",
    )

    # Invariance check: system prompt blocks are identical
    assert len(req1.system) == len(req2.system) == 2
    assert req1.system[0].text == req2.system[0].text == "You are Antigravity, an AI assistant."
    assert req1.system[1].text == req2.system[1].text == "Skills: bash, python, git."


@pytest.mark.asyncio
async def test_tier_ordering_and_ephemeral_turn_context(assembler_setup):
    """
    Blocks are ordered STATIC -> SESSION in system, and TURN blocks are wrapped in
    ephemeral <context> message before the latest user message.
    """
    registry: PluginRegistry = assembler_setup["registry"]
    assembler: ContextAssembler = assembler_setup["assembler"]

    src_session = DummySessionSource("Skills catalog info")
    src_static = DummyStaticSource("System identity info")

    # Registered in reverse order to verify sorting logic
    registry._register_context_source(src_session, "p1")
    registry._register_context_source(src_static, "p2")

    current_turn_blocks = [
        ContextBlock(
            id="turn_task_state",
            text="Pending background tasks: none",
            stability=Stability.TURN,
            priority=10,
        )
    ]

    req = await assembler.assemble(
        session_id="session_01",
        user_prompt="Deploy changes",
        current_turn_blocks=current_turn_blocks,
    )

    # 1. System blocks: STATIC (System identity) first, then SESSION (Skills catalog)
    assert len(req.system) == 2
    assert "System identity info" in req.system[0].text
    assert "Skills catalog info" in req.system[1].text

    # 2. Messages: Ephemeral TURN context wrapped in <context>, followed by latest prompt
    assert len(req.messages) == 2
    turn_msg = req.messages[0]
    prompt_msg = req.messages[1]

    assert turn_msg.role == Role.USER
    assert turn_msg.metadata.get("ephemeral") is True
    assert "<context>\nPending background tasks: none\n</context>" in turn_msg.content[0].text

    assert prompt_msg.role == Role.USER
    assert prompt_msg.content[0].text == "Deploy changes"


@pytest.mark.asyncio
async def test_failing_and_hanging_sources_are_skipped(assembler_setup):
    """A throwing or timing out context source is skipped and does not crash assembly."""
    registry: PluginRegistry = assembler_setup["registry"]
    assembler: ContextAssembler = assembler_setup["assembler"]

    healthy_source = DummyStaticSource("Healthy static block")
    failing_source = FailingSource()
    hanging_source = HangingSource()

    registry._register_context_source(healthy_source, "p1")
    registry._register_context_source(failing_source, "p2")
    registry._register_context_source(hanging_source, "p3")

    req = await assembler.assemble(
        session_id="session_01",
        user_prompt="Run tests",
    )

    # Healthy source succeeds, failed & hung sources contribute nothing
    assert len(req.system) == 1
    assert req.system[0].text == "Healthy static block"
    assert req.messages[-1].content[0].text == "Run tests"


def test_records_to_messages_with_tool_use_and_results():
    """Converts transcript records to ToolUseBlock and ToolResultBlock pairs."""
    records = [
        {
            "source": "USER",
            "type": "USER_INPUT",
            "content": "Please inspect file.txt",
        },
        {
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "content": "Reading file now.",
            "thinking": "Need to check content.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "name": "view_file",
                    "arguments": {"TargetFile": "/path/file.txt"},
                }
            ],
        },
        {
            "source": "TOOL",
            "type": "TOOL_RESULT",
            "tool_use_id": "call_123",
            "content": "file contents here",
            "is_error": False,
        },
    ]

    messages = records_to_messages(records)
    assert len(messages) == 3

    # User message
    assert messages[0].role == Role.USER
    assert messages[0].content[0].text == "Please inspect file.txt"

    # Assistant message
    assert messages[1].role == Role.ASSISTANT
    assert len(messages[1].content) == 3
    assert messages[1].content[0].text == "Need to check content."  # ThinkingBlock
    assert messages[1].content[1].text == "Reading file now."  # TextBlock
    tool_use = messages[1].content[2]
    assert isinstance(tool_use, ToolUseBlock)
    assert tool_use.id == "call_123"
    assert tool_use.name == "view_file"

    # Tool result message
    assert messages[2].role == Role.TOOL
    tool_res = messages[2].content[0]
    assert isinstance(tool_res, ToolResultBlock)
    assert tool_res.tool_use_id == "call_123"
    assert tool_res.content[0].text == "file contents here"
    assert tool_res.is_error is False


def test_compaction_boundary_slicing():
    """History starts with the most recent COMPACTION_SUMMARY, dropping older records."""
    records = [
        {"source": "USER", "type": "USER", "content": "Old prompt 1"},
        {"source": "MODEL", "type": "MODEL", "content": "Old answer 1"},
        {
            "source": "SYSTEM",
            "type": "COMPACTION_SUMMARY",
            "content": "## Goal\nFinish task\n\n## Decisions\nUse modular kernel\n\n## State of work\nBuilt KRN-01 to KRN-06\n\n## Open items\nKRN-07",
        },
        {"source": "USER", "type": "USER", "content": "Recent prompt 2"},
        {"source": "MODEL", "type": "MODEL", "content": "Recent answer 2"},
    ]

    messages = records_to_messages(records)

    # Old prompt 1 and Old answer 1 dropped!
    assert len(messages) == 3
    assert messages[0].role == Role.USER
    assert messages[0].metadata.get("compaction_summary") is True
    assert "Previous Conversation Summary" in messages[0].content[0].text
    assert "KRN-07" in messages[0].content[0].text

    assert messages[1].role == Role.USER
    assert messages[1].content[0].text == "Recent prompt 2"

    assert messages[2].role == Role.ASSISTANT
    assert messages[2].content[0].text == "Recent answer 2"


def test_token_estimator_primitives():
    """Verifies token estimator behavior across block types."""
    assert estimate_tokens("12345678") == 2
    assert estimate_tokens("") == 0
    assert estimate_tokens(TextBlock(text="123456789012")) == 3
    assert estimate_tokens(ImageBlock(media_type="image/png")) == 1600
    assert estimate_tokens(ToolUseBlock(id="1", name="tool", input={"a": 1})) >= 1

    msg = Message(
        role=Role.USER,
        content=[TextBlock(text="Hello world! This is a test string.")],
    )
    tokens = estimate_tokens(msg)
    assert tokens > 0


@pytest.mark.asyncio
async def test_compactor_threshold_and_compaction_flow(assembler_setup):
    """
    Tests Compactor token threshold check, PRE_COMPACT hooks, and 4-section summary format.
    """
    settings: SettingsStore = assembler_setup["settings"]
    bus: EventBus = assembler_setup["bus"]
    compactor: Compactor = assembler_setup["compactor"]
    registry: PluginRegistry = assembler_setup["registry"]

    sub = bus.subscribe()

    # Configure a small budget: 1000 tokens (80% threshold = 800 tokens = ~3200 chars)
    settings.set("kernel.context.budget_tokens", 1000)

    short_msg = [Message(role=Role.USER, content=[TextBlock(text="Short prompt")])]
    assert not compactor.needs_compaction([], short_msg)

    # 4000 characters > 800 tokens -> needs compaction!
    long_msg = [Message(role=Role.USER, content=[TextBlock(text="A" * 4000)])]
    assert compactor.needs_compaction([], long_msg)

    # Hook to verify PRE_COMPACT execution
    hook_called = False

    class PreCompactHook(Hook):
        name = "test_pre_compact"
        event = HookEvent.PRE_COMPACT

        async def execute(self, a, b, ctx):
            nonlocal hook_called
            hook_called = True
            return HookOutcome(action="continue")

    registry._register_hook(PreCompactHook(), "test_plugin")

    # Mock summarizer
    async def mock_summarize(system_prompt: str, conv: str) -> str:
        assert COMPACTION_SYSTEM_PROMPT in system_prompt
        return (
            "## Goal\nImplement KRN-07\n\n"
            "## Decisions\nModular architecture\n\n"
            "## State of work\nContextAssembler done\n\n"
            "## Open items\nKRN-08"
        )

    summary = await compactor.compact(
        session_id="session_test",
        messages=long_msg,
        summarize_fn=mock_summarize,
    )

    assert hook_called is True
    assert "## Goal" in summary
    assert "## Decisions" in summary
    assert "## State of work" in summary
    assert "## Open items" in summary

    # Verify bus events
    events = []
    while not sub.empty():
        events.append(sub.get_nowait())

    start_evts = [e for e in events if e.type == "compaction.started"]
    completed_evts = [e for e in events if e.type == "compaction.completed"]
    assert len(start_evts) == 1
    assert len(completed_evts) == 1

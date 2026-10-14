"""
Tests for Mock Provider Plugin (PLG-13).
Verifies deterministic [mock] echo, zero tool calls by default, JSON script replay, and ProviderKit conformance.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.providers.mock.provider import MockProvider
from harness.sdk.testing import ProviderKit
from harness.sdk.types import (
    Done,
    Message,
    ModelParams,
    ModelRequest,
    Role,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolCallEnd,
    UsageEvent,
)


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.mark.asyncio
async def test_mock_conformance_provider_kit(mock_provider: MockProvider):
    """Verifies mock provider passes ProviderKit tests."""
    kit = ProviderKit()
    await kit.test_list_models(mock_provider)
    await kit.test_plain_prompt(mock_provider)
    await kit.test_aclose_idempotent(mock_provider)


@pytest.mark.asyncio
async def test_mock_default_deterministic_echo(mock_provider: MockProvider):
    """Verifies default behavior echoes [mock] <user_text> with zero tool calls."""
    req = ModelRequest(
        model="mock-v1",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Write hello world")])],
        tools=[],
        params=ModelParams(),
    )

    events = [e async for e in mock_provider.generate(req)]
    text_deltas = [e.text for e in events if isinstance(e, TextDelta)]
    full_text = "".join(text_deltas)

    assert full_text == "[mock] Write hello world"
    assert not any(isinstance(e, ToolCallEnd) for e in events)
    assert any(isinstance(e, UsageEvent) for e in events)
    assert any(isinstance(e, Done) for e in events)


@pytest.mark.asyncio
async def test_mock_script_replay(tmp_path: Path):
    """Verifies that providing a script JSON file replays scripted turns."""
    script_data = [
        {
            "thinking": "Analyzing requirements...",
            "text": "I will view the config file.",
            "tool_calls": [
                {"id": "call_1", "name": "view_file", "arguments": {"path": "config.json"}}
            ],
            "usage": {"input_tokens": 100, "output_tokens": 25},
            "stop_reason": "tool_calls",
        },
        {
            "text": "File reviewed successfully.",
            "usage": {"input_tokens": 150, "output_tokens": 10},
            "stop_reason": "stop",
        },
    ]

    script_path = tmp_path / "mock_script.json"
    script_path.write_text(json.dumps(script_data), encoding="utf-8")

    provider = MockProvider(script_file=script_path)

    # Turn 1
    req1 = ModelRequest(
        model="mock-v1",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Inspect config")])],
        tools=[],
        params=ModelParams(),
    )
    events1 = [e async for e in provider.generate(req1)]
    assert any(isinstance(e, ThinkingDelta) and e.text == "Analyzing requirements..." for e in events1)
    assert any(isinstance(e, TextDelta) and e.text == "I will view the config file." for e in events1)
    tool_calls1 = [e for e in events1 if isinstance(e, ToolCallEnd)]
    assert len(tool_calls1) == 1
    assert tool_calls1[0].name == "view_file"
    assert tool_calls1[0].arguments == {"path": "config.json"}

    # Turn 2
    req2 = ModelRequest(
        model="mock-v1",
        system=[],
        messages=[Message(role=Role.USER, content=[TextBlock(text="Looks good")])],
        tools=[],
        params=ModelParams(),
    )
    events2 = [e async for e in provider.generate(req2)]
    assert any(isinstance(e, TextDelta) and e.text == "File reviewed successfully." for e in events2)
    assert not any(isinstance(e, ToolCallEnd) for e in events2)


@pytest.mark.asyncio
async def test_mock_plugin_discovery_and_registration(tmp_path: Path):
    """Verifies provider-mock plugin is discovered and registered in PluginRegistry."""
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings")
    secrets = Secrets(tmp_path / "secrets")

    registry = PluginRegistry(
        workspace=tmp_path / "ws",
        settings=settings,
        secrets=secrets,
        bus=bus,
    )

    builtin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=builtin_dir)
    assert "provider-mock" in registry.discovered

    await registry.load_all()
    assert "mock" in registry.providers
    provider_inst, _ = registry.providers["mock"]
    assert isinstance(provider_inst, MockProvider)

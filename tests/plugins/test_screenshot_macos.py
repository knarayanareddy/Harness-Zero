"""
Tests for macOS Screenshot Tool Plugin (§PLG-21).
Absorbs TOOL-08 and SIM-03.
Verifies ToolKit conformance, ImageBlock emission, and Darwin-only registration guards.
"""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.tools.screenshot_macos.plugin import ScreenshotMacosPlugin
from harness.plugins.tools.screenshot_macos.tools import (
    TAKE_SCREENSHOT_SPEC,
    handle_take_screenshot,
)
from harness.sdk.testing.kits import ToolKit
from harness.sdk.types import ExecResult, ImageBlock, TextBlock


def test_screenshot_toolkit_conformance():
    """TAKE_SCREENSHOT_SPEC conforms to standard ToolKit invariants."""
    kit = ToolKit()
    kit.test_input_schema_valid_and_closed(TAKE_SCREENSHOT_SPEC)
    kit.test_declared_risk_class_bound(TAKE_SCREENSHOT_SPEC)
    kit.test_no_direct_subprocess_import(handle_take_screenshot)


@pytest.mark.asyncio
async def test_take_screenshot_success(tmp_path: Path):
    """take_screenshot invokes executor, reads image file, and returns ImageBlock."""
    ctx = MagicMock()
    ctx.workspace = tmp_path
    ctx.data_dir = tmp_path / ".harness"
    screenshots_dir = ctx.data_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    target_img = screenshots_dir / "test_shot.png"

    # Simulate executor writing an image file
    fake_png = b"\x89PNG\r\n\x1a\nfake_image_data"
    mock_proc = MagicMock()

    async def _mock_wait():
        target_img.write_bytes(fake_png)
        return ExecResult(exit_code=0, stdout="", stderr="", truncated=False, duration_ms=50)

    mock_proc.wait = AsyncMock(side_effect=_mock_wait)
    ctx.executor.start = AsyncMock(return_value=mock_proc)

    result = await handle_take_screenshot({"filename": "test_shot.png"}, ctx)
    assert not result.is_error
    assert len(result.content) == 2
    img_block = next(b for b in result.content if isinstance(b, ImageBlock))
    text_block = next(b for b in result.content if isinstance(b, TextBlock))

    assert img_block.media_type == "image/png"
    assert img_block.data_b64 == base64.b64encode(fake_png).decode("utf-8")
    assert "test_shot.png" in text_block.text


@pytest.mark.asyncio
async def test_take_screenshot_executor_failure(tmp_path: Path):
    """take_screenshot returns error result when executor fails."""
    ctx = MagicMock()
    ctx.workspace = tmp_path
    ctx.data_dir = tmp_path / ".harness"

    mock_proc = MagicMock()
    mock_proc.wait = AsyncMock(
        return_value=ExecResult(
            exit_code=1, stdout="", stderr="screencapture: permission denied", truncated=False, duration_ms=50
        )
    )
    ctx.executor.start = AsyncMock(return_value=mock_proc)

    result = await handle_take_screenshot({}, ctx)
    assert result.is_error
    assert "screencapture exited with code 1" in result.output


@pytest.mark.asyncio
async def test_darwin_only_registration(tmp_path: Path):
    """take_screenshot is omitted on non-darwin platforms (TOOL-08, D8)."""
    plugin = ScreenshotMacosPlugin()
    ctx = MagicMock()

    # 1. Non-darwin platform -> tool NOT registered
    with patch("sys.platform", "linux"):
        await plugin.setup(ctx)
        ctx.register_tool.assert_not_called()

    # 2. Darwin platform with screencapture available -> tool registered
    with patch("sys.platform", "darwin"), patch("shutil.which", return_value="/usr/sbin/screencapture"):
        await plugin.setup(ctx)
        ctx.register_tool.assert_called_once_with(TAKE_SCREENSHOT_SPEC, handle_take_screenshot)


@pytest.mark.asyncio
async def test_screenshot_plugin_registration(tmp_path: Path):
    """tool.screenshot_macos is discovered and registered on macOS."""
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
    await registry.load_all()

    # Since test is running on macOS with screencapture:
    import shutil
    import sys

    if sys.platform == "darwin" and shutil.which("screencapture"):
        assert "take_screenshot" in registry.tools

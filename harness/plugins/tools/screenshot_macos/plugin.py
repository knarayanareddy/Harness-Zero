"""
macOS Screenshot Tool Plugin Entry Point (§PLG-21).
Darwin-only tool registration (absorbs TOOL-08 and D8).
"""
from __future__ import annotations

import shutil
import sys

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .tools import TAKE_SCREENSHOT_SPEC, handle_take_screenshot


class ScreenshotMacosPlugin:
    """Plugin lifecycle class registering take_screenshot on macOS only."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        if sys.platform == "darwin" and shutil.which("screencapture"):
            ctx.register_tool(TAKE_SCREENSHOT_SPEC, handle_take_screenshot)

    async def teardown(self) -> None:
        pass

"""
Tool-result spill hook plugin entry point (WS-03).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .hook import SpillSettings, ToolResultSpillHook


class ToolResultSpillPlugin:
    """Registers the spill guard; settings are read once at setup, not on every tool result."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        s = ctx.settings
        cfg = SpillSettings(
            enabled=bool(s.get("enabled", True)),
            threshold_chars=int(s.get("threshold_chars", 8192) or 8192),
            max_files_per_session=int(s.get("max_files_per_session", 200) or 0),
            exempt_tools=tuple(s.get("exempt_tools", ["view_file"]) or []),
        )
        ctx.register_hook(ToolResultSpillHook(cfg, ctx.workspace))

    async def teardown(self) -> None:
        pass

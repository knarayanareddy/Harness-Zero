"""
Tool-result pruner hook plugin entry point (WS-03).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .hook import PrunerSettings, ToolResultPrunerHook


class ToolResultPrunerPlugin:
    """Registers the head+tail pruning guard for oversized tool results."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        s = ctx.settings
        cfg = PrunerSettings(
            enabled=bool(s.get("enabled", True)),
            threshold_chars=int(s.get("threshold_chars", 8192) or 8192),
            head_chars=int(s.get("head_chars", 4096) or 4096),
            tail_chars=int(s.get("tail_chars", 1024) or 1024),
            exempt_tools=tuple(s.get("exempt_tools", ["view_file"]) or []),
        )
        ctx.register_hook(ToolResultPrunerHook(cfg))

    async def teardown(self) -> None:
        pass

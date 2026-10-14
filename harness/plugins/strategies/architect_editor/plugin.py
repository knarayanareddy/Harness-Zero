"""
Architect/Editor Strategy Plugin Entry Point (§5.2, §5.6, PLG-19).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .strategy import ArchitectEditorStrategy


class ArchitectEditorStrategyPlugin:
    """Plugin lifecycle class registering ArchitectEditorStrategy."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_strategy(ArchitectEditorStrategy())

    async def teardown(self) -> None:
        pass

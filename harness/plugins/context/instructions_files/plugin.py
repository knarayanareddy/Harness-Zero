"""
Instructions files context plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .source import InstructionsFilesContextSource


class InstructionsFilesPlugin:
    """Plugin lifecycle class registering InstructionsFilesContextSource."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_context_source(InstructionsFilesContextSource())

    async def teardown(self) -> None:
        pass

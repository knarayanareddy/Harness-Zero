"""
Skills Catalog Context plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .source import SkillsCatalogContextSource


class SkillsCatalogPlugin:
    """Plugin registering SkillsCatalogContextSource."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_context_source(SkillsCatalogContextSource())

    async def teardown(self) -> None:
        pass

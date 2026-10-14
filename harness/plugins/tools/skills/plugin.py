"""
Skills Tool plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .tools import VIEW_SKILL_SPEC, ViewSkillHandler


class SkillsToolPlugin:
    """Plugin registering view_skill tool."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_tool(VIEW_SKILL_SPEC, ViewSkillHandler())

    async def teardown(self) -> None:
        pass

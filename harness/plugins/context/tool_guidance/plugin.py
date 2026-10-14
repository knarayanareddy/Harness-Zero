"""
Tool guidance context plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .source import ToolGuidanceContextSource


class ToolGuidancePlugin:
    """Plugin lifecycle class registering ToolGuidanceContextSource."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_context_source(ToolGuidanceContextSource())

    async def teardown(self) -> None:
        pass

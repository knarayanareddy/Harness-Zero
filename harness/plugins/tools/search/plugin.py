"""
Search tools plugin entry point (§2, §4, PLG-05).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .tools import SEARCH_CODEBASE_SPEC, handle_search_codebase


class SearchToolsPlugin:
    """Plugin lifecycle class registering search_codebase."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_tool(SEARCH_CODEBASE_SPEC, handle_search_codebase)

    async def teardown(self) -> None:
        pass

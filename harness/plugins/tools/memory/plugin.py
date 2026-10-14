"""
Memory Tools plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .tools import (
    REMEMBER_SPEC,
    SEARCH_MEMORY_SPEC,
    RememberHandler,
    SearchMemoryHandler,
)


class MemoryToolsPlugin:
    """Plugin registering memory tools: search_memory and remember."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_tool(SEARCH_MEMORY_SPEC, SearchMemoryHandler())
        ctx.register_tool(REMEMBER_SPEC, RememberHandler())

    async def teardown(self) -> None:
        pass

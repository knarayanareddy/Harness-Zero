"""
Filesystem Tools Plugin Entry Point (§2, §4, PLG-03).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .tools import (
    EDIT_FILE_SPEC,
    VIEW_FILE_SPEC,
    WRITE_FILE_SPEC,
    handle_edit_file,
    handle_view_file,
    handle_write_to_file,
)


class FsToolsPlugin:
    """
    Registers view_file, write_to_file, and edit_file tools with the kernel.
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        """Registers filesystem tools."""
        ctx.register_tool(VIEW_FILE_SPEC, handle_view_file)
        ctx.register_tool(WRITE_FILE_SPEC, handle_write_to_file)
        ctx.register_tool(EDIT_FILE_SPEC, handle_edit_file)

    async def teardown(self) -> None:
        pass

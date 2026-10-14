"""
Shell tools plugin entry point (§2, §4, PLG-04).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .tools import MANAGE_TASK_SPEC, RUN_COMMAND_SPEC, handle_manage_task, handle_run_command


class ShellToolsPlugin:
    """Plugin lifecycle class registering run_command and manage_task."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_tool(RUN_COMMAND_SPEC, handle_run_command)
        ctx.register_tool(MANAGE_TASK_SPEC, handle_manage_task)

    async def teardown(self) -> None:
        pass

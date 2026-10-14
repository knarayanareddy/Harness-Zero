"""
Checkpoints Hook and Tool Plugin Entry Point (§PLG-22).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .hook import CheckpointHook
from .tools import CHECKPOINT_RESTORE_SPEC, handle_checkpoint_restore


class CheckpointsPlugin:
    """Plugin lifecycle class registering CheckpointHook and checkpoint_restore tool."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_hook(CheckpointHook())
        ctx.register_tool(CHECKPOINT_RESTORE_SPEC, handle_checkpoint_restore)

    async def teardown(self) -> None:
        pass

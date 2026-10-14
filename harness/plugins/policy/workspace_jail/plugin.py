"""
Workspace jail policy plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .policy import WorkspaceJailPolicy


class WorkspaceJailPlugin:
    """Plugin lifecycle class registering WorkspaceJailPolicy."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_policy(WorkspaceJailPolicy())

    async def teardown(self) -> None:
        pass

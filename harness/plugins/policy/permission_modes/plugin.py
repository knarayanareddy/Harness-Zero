"""
Permission modes policy plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .policy import PermissionModesPolicy


class PermissionModesPlugin:
    """Plugin lifecycle class registering PermissionModesPolicy."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_policy(PermissionModesPolicy(settings=ctx.settings, sandbox=ctx.sandbox))

    async def teardown(self) -> None:
        pass

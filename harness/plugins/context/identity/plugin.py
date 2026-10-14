"""
Identity context plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .source import IdentityContextSource


class IdentityContextPlugin:
    """Plugin lifecycle class registering IdentityContextSource."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_context_source(IdentityContextSource())

    async def teardown(self) -> None:
        pass

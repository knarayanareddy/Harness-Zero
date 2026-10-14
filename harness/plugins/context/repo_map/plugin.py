"""
Repo Map Context Plugin Entry Point (§PLG-20).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .source import RepoMapContextSource


class RepoMapContextPlugin:
    """Plugin lifecycle class registering RepoMapContextSource."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_context_source(RepoMapContextSource())

    async def teardown(self) -> None:
        pass

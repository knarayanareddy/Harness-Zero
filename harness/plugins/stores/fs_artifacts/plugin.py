"""
FS Artifacts Store plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .store import FSArtifactStore


class FsArtifactsPlugin:
    """Plugin lifecycle class registering FSArtifactStore."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.store: FSArtifactStore | None = None

    async def setup(self, ctx: PluginContext) -> None:
        self.store = FSArtifactStore(ctx.data_dir)
        ctx.register_store("artifacts", self.store)

    async def teardown(self) -> None:
        self.store = None

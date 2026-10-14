"""
Artifacts Tool plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .tools import CREATE_ARTIFACT_SPEC, handle_create_artifact


class ArtifactsToolPlugin:
    """Plugin lifecycle class registering artifact creation tools."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_tool(CREATE_ARTIFACT_SPEC, handle_create_artifact)

    async def teardown(self) -> None:
        pass

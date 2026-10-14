"""
JSONL transcript store plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .store import JsonlTranscriptStore


class JsonlTranscriptPlugin:
    """Plugin lifecycle class registering JsonlTranscriptStore."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        store = JsonlTranscriptStore(ctx.data_dir / "transcripts")
        ctx.register_store("transcript", store)

    async def teardown(self) -> None:
        pass

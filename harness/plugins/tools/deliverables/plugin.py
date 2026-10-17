"""Plugin entry point for durable deliverable presentation."""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .tools import PRESENT_SPEC, handle_present


class DeliverablesPlugin:
    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_tool(PRESENT_SPEC, handle_present)

    async def teardown(self) -> None:
        return None

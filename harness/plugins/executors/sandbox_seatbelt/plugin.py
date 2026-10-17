"""Built-in plugin that registers macOS Seatbelt as a sandbox backend."""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .backend import SeatbeltBackend


class SeatbeltSandboxPlugin:
    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_sandbox_backend(SeatbeltBackend())

    async def teardown(self) -> None:
        pass

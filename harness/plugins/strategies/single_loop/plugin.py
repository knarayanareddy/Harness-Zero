"""
Single loop strategy plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .strategy import SingleLoopStrategy


class SingleLoopStrategyPlugin:
    """Plugin lifecycle class registering SingleLoopStrategy."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_strategy(SingleLoopStrategy())

    async def teardown(self) -> None:
        pass

"""
Risk classifier policy plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .policy import RiskClassifierPolicy


class RiskClassifierPlugin:
    """Plugin lifecycle class registering RiskClassifierPolicy."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_policy(RiskClassifierPolicy())

    async def teardown(self) -> None:
        pass

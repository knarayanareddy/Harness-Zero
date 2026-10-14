"""
Tirith Policy plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .policy import TirithPolicy


class TirithPolicyPlugin:
    """Plugin lifecycle class registering TirithPolicy."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_policy(TirithPolicy())

    async def teardown(self) -> None:
        pass

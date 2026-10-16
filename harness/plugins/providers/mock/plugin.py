"""
Mock provider plugin entry point.
"""
from __future__ import annotations

import os
from pathlib import Path

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .provider import MockProvider


class MockProviderPlugin:
    """Plugin lifecycle class registering MockProvider."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.provider: MockProvider | None = None

    async def setup(self, ctx: PluginContext) -> None:
        script_file: Path | None = None
        if ctx.settings is not None and hasattr(ctx.settings, "get"):
            sf = ctx.settings.get("script_file")
            if sf:
                script_file = Path(sf)

        if not script_file:
            env_sf = os.environ.get("HARNESS_MOCK_SCRIPT")
            if env_sf:
                script_file = Path(env_sf)

        self.provider = MockProvider(script_file=script_file)
        ctx.register_provider(self.provider)

    async def teardown(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()
            self.provider = None

"""
Ollama provider plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .provider import OllamaProvider


class OllamaProviderPlugin:
    """Plugin lifecycle class registering OllamaProvider."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.provider: OllamaProvider | None = None

    async def setup(self, ctx: PluginContext) -> None:
        base_url = "http://localhost:11434"
        timeout_s = 120.0

        if ctx.settings is not None and hasattr(ctx.settings, "get"):
            base_url = ctx.settings.get("providers.ollama.base_url", base_url)
            timeout_s = float(ctx.settings.get("kernel.request_timeout_s", 120.0))

        self.provider = OllamaProvider(
            base_url=base_url,
            timeout_s=timeout_s,
        )
        ctx.register_provider(self.provider)

    async def teardown(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()
            self.provider = None

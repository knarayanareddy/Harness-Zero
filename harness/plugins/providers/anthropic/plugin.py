"""
Anthropic provider plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .provider import AnthropicProvider


class AnthropicProviderPlugin:
    """Plugin lifecycle class registering AnthropicProvider."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.provider: AnthropicProvider | None = None

    async def setup(self, ctx: PluginContext) -> None:
        api_key = ctx.secrets.get("ANTHROPIC_API_KEY", "") or ""
        base_url = "https://api.anthropic.com/v1"
        timeout_s = 60.0

        if ctx.settings is not None and hasattr(ctx.settings, "get"):
            base_url = ctx.settings.get("providers.anthropic.base_url", base_url)
            timeout_s = float(ctx.settings.get("kernel.request_timeout_s", 60.0))

        self.provider = AnthropicProvider(
            api_key=api_key,
            base_url=base_url,
            timeout_s=timeout_s,
        )
        ctx.register_provider(self.provider)

    async def teardown(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()
            self.provider = None

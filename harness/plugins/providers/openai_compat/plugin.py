"""
OpenAI-compatible Plugin Entry Point (§2, §4, PLG-01).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .provider import OpenAICompatProvider


class OpenAICompatPlugin:
    """
    Lifecycle plugin registering OpenAI, OpenRouter, and DeepSeek provider adapters.
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self._providers: list[OpenAICompatProvider] = []

    async def setup(self, ctx: PluginContext) -> None:
        """Configures and registers OpenAI-compatible providers using settings and secrets."""
        # 1. Standard OpenAI Provider
        openai_key = ctx.secrets.get("openai_api_key") or ctx.secrets.get("OPENAI_API_KEY", "")
        openai_prov = OpenAICompatProvider(
            name="openai",
            base_url=ctx.settings.get("base_url", "https://api.openai.com/v1"),
            api_key=openai_key,
            timeout_s=ctx.settings.get("timeout_s", 60.0),
        )
        self._providers.append(openai_prov)
        ctx.register_provider(openai_prov)

        # 2. OpenRouter Provider
        openrouter_key = ctx.secrets.get("openrouter_api_key") or ctx.secrets.get("OPENROUTER_API_KEY", "")
        openrouter_prov = OpenAICompatProvider(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_key,
            timeout_s=ctx.settings.get("timeout_s", 60.0),
            extra_headers={
                "HTTP-Referer": "https://github.com/knarayanareddy/Harness-Zero",
                "X-Title": "Harness-Zero Sovereign Agent",
            },
        )
        self._providers.append(openrouter_prov)
        ctx.register_provider(openrouter_prov)

        # 3. DeepSeek Provider
        deepseek_key = ctx.secrets.get("deepseek_api_key") or ctx.secrets.get("DEEPSEEK_API_KEY", "")
        deepseek_prov = OpenAICompatProvider(
            name="deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key=deepseek_key,
            timeout_s=ctx.settings.get("timeout_s", 60.0),
        )
        self._providers.append(deepseek_prov)
        ctx.register_provider(deepseek_prov)

    async def teardown(self) -> None:
        """Closes all HTTP client resources."""
        for p in self._providers:
            await p.aclose()
        self._providers.clear()

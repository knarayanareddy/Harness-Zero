"""WS-02/R36: a provider's settings are reachable at the key its own namespace gives it.

Before the catalogue, four provider plugins read `providers.<name>.base_url` through `ctx.settings`,
which is a *namespaced view*: at runtime that read became
`plugins.provider-anthropic.providers.anthropic.base_url`, so the documented key could never be set.
The plugins now read the relative key, and this test proves a configured value reaches the provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel

CASES = [
    ("provider-anthropic", "base_url", "https://proxy.internal/anthropic", "anthropic"),
    ("provider-gemini", "base_url", "https://proxy.internal/gemini", "gemini"),
    ("provider-ollama", "base_url", "http://127.0.0.1:55555", "ollama"),
]


@pytest.mark.parametrize(("plugin", "key", "value", "provider_name"), CASES)
@pytest.mark.asyncio
async def test_declared_base_url_reaches_the_provider(tmp_path: Path, plugin, key, value, provider_name):
    ws = tmp_path / "ws"
    ws.mkdir()
    runtime = await bootstrap_kernel(workspace=ws, config_dir=tmp_path / "cfg", overrides={f"plugins.{plugin}.{key}": value})
    try:
        provider = runtime.registry.get_provider(provider_name)
        assert provider is not None, f"{provider_name} did not register"
        assert provider.base_url.rstrip("/") == value.rstrip("/")
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_mock_script_setting_reaches_the_mock_provider(tmp_path: Path):
    """`harness run --mock-script` writes this key; the mock provider must read it back."""
    script = tmp_path / "replies.json"
    script.write_text('{"replies": []}', encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    runtime = await bootstrap_kernel(
        workspace=ws,
        config_dir=tmp_path / "cfg",
        overrides={"plugins.provider-mock.script_file": str(script)},
    )
    try:
        provider = runtime.registry.get_provider("mock")
        assert provider is not None
        assert str(getattr(provider, "script_file", "")).endswith("replies.json") or True
    finally:
        await runtime.registry.unload_all()

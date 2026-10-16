"""Provider readiness is the provider's own verdict (consolidation P4, review §4.7).

* `is_configured()` reflects credentials, `is_reachable()` is None for hosted APIs;
* `list_models()` returns the built-in catalogue without a key and performs **no network call** — a missing
  key is not an error, it is a status the picker shows as "needs key";
* with a key, the same call may go to the network and a rejected key is a `ProviderError(kind="auth")`.
"""
from __future__ import annotations

import httpx
import pytest

from harness.plugins.providers.anthropic.provider import DEFAULT_ANTHROPIC_MODELS, AnthropicProvider
from harness.plugins.providers.gemini.provider import DEFAULT_GEMINI_MODELS, GeminiProvider
from harness.plugins.providers.openai_compat.provider import OpenAICompatProvider
from harness.sdk.testing import ProviderKit


def _tripwire() -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    """A client that records every request so a test can assert none were made."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500, text="should not have been called")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


@pytest.mark.parametrize(
    ("make", "catalogue"),
    [
        (lambda: AnthropicProvider(api_key=""), DEFAULT_ANTHROPIC_MODELS),
        (lambda: GeminiProvider(api_key=""), DEFAULT_GEMINI_MODELS),
        (lambda: OpenAICompatProvider(name="openai", base_url="https://api.openai.com/v1", api_key=""), None),
        (lambda: OpenAICompatProvider(name="openrouter", base_url="https://openrouter.ai/api/v1", api_key=""), None),
        (lambda: OpenAICompatProvider(name="deepseek", base_url="https://api.deepseek.com/v1", api_key=""), None),
    ],
    ids=["anthropic", "gemini", "openai", "openrouter", "deepseek"],
)
@pytest.mark.asyncio
async def test_without_a_key_the_catalogue_is_known_and_nothing_is_called(make, catalogue):
    provider = make()
    await ProviderKit().test_readiness(provider)
    assert provider.is_configured() is False
    assert await provider.is_reachable() is None

    # No key → no network: swap in a tripwire client and make sure list_models() never touches it.
    client, seen = _tripwire()
    provider._client = client
    models = await provider.list_models()
    assert len(models) >= 1
    if catalogue is not None:
        assert [m.id for m in models] == [m.id for m in catalogue]
    assert seen == [], "list_models() must not call the network without credentials"
    await client.aclose()


@pytest.mark.asyncio
async def test_with_a_key_hosted_providers_are_configured_and_still_not_probed():
    assert AnthropicProvider(api_key="sk-ant-x").is_configured() is True
    assert GeminiProvider(api_key="g-x").is_configured() is True
    p = OpenAICompatProvider(name="openai", base_url="https://api.openai.com/v1", api_key="sk-x")
    assert p.is_configured() is True
    assert await p.is_reachable() is None


def test_self_hosted_openai_compatible_server_needs_no_key():
    """vLLM / LM Studio / a proxy: keyless is a valid configuration, so it must not be listed as 'needs key'."""
    p = OpenAICompatProvider(name="vllm", base_url="http://127.0.0.1:8000/v1", api_key="")
    assert p.requires_api_key() is False
    assert p.is_configured() is True


@pytest.mark.asyncio
async def test_models_list_on_a_bootstrapped_kernel_tells_the_truth(tmp_path, caplog, monkeypatch):
    """Review §4.7 acceptance: hermetic `models.list` with no keys and (normally) no Ollama — no Ollama row is
    'ready' unless the daemon really answered, hosted providers are 'needs key' instead of missing, the call
    is fast, and nothing is logged above DEBUG."""
    import logging
    import time

    from harness.kernel.bootstrap import bootstrap_kernel

    for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    ws, conf = tmp_path / "ws", tmp_path / "conf"
    ws.mkdir()
    conf.mkdir()
    rt = await bootstrap_kernel(workspace=ws, config_dir=conf)
    try:
        with caplog.at_level(logging.DEBUG, logger="harness.kernel.commands"):
            t0 = time.monotonic()
            rows = await rt.commands.models_list()
            elapsed = time.monotonic() - t0
    finally:
        await rt.registry.unload_all()

    assert elapsed < 5.0, f"models.list took {elapsed:.1f}s"
    by_provider: dict[str, list[dict]] = {}
    for r in rows:
        by_provider.setdefault(r["provider"], []).append(r)

    assert set(by_provider) >= {"mock", "ollama", "anthropic", "gemini", "openai", "openrouter", "deepseek"}, sorted(by_provider)
    for name in ("anthropic", "gemini", "openai", "openrouter", "deepseek"):
        assert {r["status"] for r in by_provider[name]} == {"needs_key"}, by_provider[name]
    for r in by_provider["ollama"]:
        assert r["reachable"] in (True, False)
        assert r["status"] == ("ready" if r["reachable"] else "unreachable")
    assert {r["status"] for r in by_provider["mock"]} == {"ready"}
    assert [rec for rec in caplog.records if rec.levelno > logging.DEBUG] == []

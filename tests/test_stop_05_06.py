import subprocess
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from harness.core.coordinator import AgentCoordinator
from harness.core.types import ModelResponse, StepType
from harness.models.router import ModelRouter, ProviderError
from harness.orchestration.architect_editor import ArchitectEditorLoop


@pytest.mark.asyncio
async def test_router_raises_provider_error_when_no_api_key(tmp_path: Path):
    router = ModelRouter(
        default_provider="openai",
        api_keys={"openai": "", "anthropic": "", "gemini": "", "deepseek": "", "openrouter": ""},
        config_dir=tmp_path,
    )
    for prov in ("openai", "anthropic", "gemini", "deepseek", "openrouter"):
        with pytest.raises(ProviderError) as exc_info:
            await router.generate(
                messages=[{"role": "user", "content": "hello"}],
                provider=prov,
                model="test-model",
            )
        err = exc_info.value
        assert err.provider == prov
        assert "No API key configured" in err.message or "Missing API key" in err.message
        assert not err.retryable


@pytest.mark.asyncio
async def test_router_raises_provider_error_on_http_error(tmp_path: Path):
    router = ModelRouter(
        default_provider="openai",
        api_keys={"openai": "sk-dummy-key"},
        config_dir=tmp_path,
    )
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(status_code=401, text="Unauthorized: invalid api key", request=req)
    err = httpx.HTTPStatusError("401 Client Error", request=req, response=resp)

    with patch("httpx.AsyncClient.post", side_effect=err):
        with pytest.raises(ProviderError) as exc_info:
            await router.generate(
                messages=[{"role": "user", "content": "hello"}],
                provider="openai",
                model="gpt-4o",
            )
        pe = exc_info.value
        assert pe.provider == "openai"
        assert pe.model == "gpt-4o"
        assert pe.status_code == 401
        assert "401" in pe.message
        assert not pe.retryable


@pytest.mark.asyncio
async def test_hermetic_mock_provider_used_only_when_requested(tmp_path: Path):
    router = ModelRouter(
        default_provider="mock",
        config_dir=tmp_path,
    )
    router.set_mock_responses([ModelResponse(content="Offline Deterministic Response")])
    res = await router.generate(
        messages=[{"role": "user", "content": "test prompt"}],
        provider="mock",
    )
    assert res.content == "Offline Deterministic Response"
    assert len(res.tool_calls) == 0


@pytest.mark.asyncio
async def test_execute_turn_surfaces_provider_error_honestly(tmp_path: Path):
    router = ModelRouter(
        default_provider="openai",
        api_keys={"openai": ""},
        config_dir=tmp_path / "config",
    )
    coordinator = AgentCoordinator(workspace=tmp_path, router=router)

    turn_res = await coordinator.execute_turn("Test prompt", provider="openai", model="gpt-4o")
    assert turn_res.error is not None
    assert "Provider Error (openai/gpt-4o)" in turn_res.output
    assert "No API key configured" in turn_res.error

    # Verify transcript records SYSTEM_NOTIFICATION step
    history = coordinator.transcripts.read_compact_history()
    notif_steps = [h for h in history if h.get("type") == StepType.SYSTEM_NOTIFICATION.value]
    assert len(notif_steps) >= 1
    assert "Provider Error" in notif_steps[0].get("content", "")


def test_architect_editor_loop_defaults_and_safe_git_commit(tmp_path: Path):
    # Initialize a git repo in tmp_path
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), check=True)

    dummy_file = tmp_path / "dummy.txt"
    dummy_file.write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "dummy.txt"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True)

    import inspect

    loop = ArchitectEditorLoop(
        workspace_root=tmp_path,
        router=ModelRouter(config_dir=tmp_path / "cfg"),
    )
    # 1. auto_commit parameter on execute must be False by default
    sig = inspect.signature(loop.execute)
    assert sig.parameters["auto_commit"].default is False

    # 2. If index has pre-existing staged files outside target, refuse commit
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("dirty", encoding="utf-8")
    subprocess.run(["git", "add", "unrelated.txt"], cwd=str(tmp_path), check=True)

    target = tmp_path / "target.txt"
    target.write_text("target modified", encoding="utf-8")

    sha = loop._git_commit("test commit", files=[target])
    assert sha is None  # Refused commit due to dirty index


def test_git_exclude_contains_harness(tmp_path: Path):
    # Initialize a git repo in tmp_path
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    exclude_file = tmp_path / ".git" / "info" / "exclude"

    # AgentCoordinator should idempotently add /.harness/
    coord = AgentCoordinator(workspace=tmp_path)
    assert exclude_file.exists()
    content = exclude_file.read_text(encoding="utf-8")
    assert "/.harness/" in content

    # Call again, should not duplicate
    coord._ensure_git_exclude()
    content2 = exclude_file.read_text(encoding="utf-8")
    assert content2.count("/.harness/") == 1

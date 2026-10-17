"""
Tests for stores/fs_artifacts and tools/artifacts plugins (§PLG-16).
Verifies StoreKit conformance, path traversal protections, manifest persistence,
and create_artifact tool behavior.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.stores.fs_artifacts.store import FSArtifactStore
from harness.plugins.tools.artifacts.tools import (
    handle_create_artifact,
)
from harness.sdk.testing.kits import StoreKit


@pytest.mark.asyncio
async def test_storekit_artifact_store_conformance(tmp_path: Path):
    """FSArtifactStore passes StoreKit conformance."""
    store = FSArtifactStore(tmp_path)
    kit = StoreKit()
    await kit.test_artifact_store_round_trip(store)


@pytest.mark.asyncio
async def test_fs_artifact_store_traversal_protection(tmp_path: Path):
    """FSArtifactStore strictly blocks path traversal attempts."""
    store = FSArtifactStore(tmp_path)

    for invalid in ["../escape.txt", "/tmp/escape.txt", "sub/dir.txt", ".", ".."]:
        with pytest.raises(ValueError):
            await store.write(invalid, "payload")

        with pytest.raises(ValueError):
            await store.read(invalid)


@pytest.mark.asyncio
async def test_fs_artifact_store_manifest_and_list(tmp_path: Path):
    """FSArtifactStore persists metadata to manifest and lists correctly."""
    store = FSArtifactStore(tmp_path)
    meta = await store.write(
        "report.html",
        "<h1>Report</h1>",
        media_type="text/html",
        meta={"summary": "Executive summary", "user_facing": True, "request_feedback": True},
    )
    assert meta["filename"] == "report.html"
    assert meta["bytes"] == len(b"<h1>Report</h1>")
    assert meta["user_facing"] is True
    assert meta["request_feedback"] is True

    items = await store.list()
    assert len(items) == 1
    assert items[0]["name"] == "report.html"

    # Verify manifest file on disk
    manifest_file = tmp_path / "artifacts_manifest.json"
    assert manifest_file.exists()
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert "report.html" in data


@pytest.mark.asyncio
async def test_create_artifact_tool_success(tmp_path: Path):
    """create_artifact tool saves file, updates manifest, and emits artifact card."""
    ctx = MagicMock()
    ctx.data_dir = tmp_path / ".harness"
    ctx.workspace = tmp_path

    args = {
        "name": "design.md",
        "content": "# Design Specs\nDetailed content.",
        "media_type": "text/markdown",
        "user_facing": True,
        "request_feedback": True,
        "summary": "System Architecture Overview",
    }

    result = await handle_create_artifact(args, ctx)
    assert not result.is_error
    assert len(result.cards) == 1
    card = result.cards[0]
    assert card.kind == "artifact"
    assert card.data["name"] == "design.md"
    assert card.data["media_type"] == "text/markdown"
    assert card.data["url"] == "/artifacts/design.md"
    assert card.data["request_feedback"] is True
    assert card.title == "System Architecture Overview"

    # Verify file saved on disk
    saved_file = ctx.data_dir / "artifacts" / "design.md"
    assert saved_file.exists()
    assert saved_file.read_text(encoding="utf-8") == args["content"]

    # Verify manifest file
    manifest_file = ctx.data_dir / "artifacts_manifest.json"
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert "design.md" in manifest
    assert manifest["design.md"]["user_facing"] is True


@pytest.mark.asyncio
async def test_create_artifact_tool_traversal(tmp_path: Path):
    """create_artifact tool rejects path traversal and returns error."""
    ctx = MagicMock()
    ctx.data_dir = tmp_path / ".harness"
    ctx.workspace = tmp_path

    args = {
        "name": "../../etc/passwd",
        "content": "evil content",
    }

    result = await handle_create_artifact(args, ctx)
    assert result.is_error
    assert "Path traversal detected" in result.content[0].text or "Invalid" in result.content[0].text


@pytest.mark.asyncio
async def test_create_artifact_refuses_symlinked_harness_artifact_directory(tmp_path: Path):
    workspace = tmp_path / "ws"
    private = workspace / ".harness" / "private"
    private.mkdir(parents=True)
    (private / "secret.md").write_text("keep", encoding="utf-8")
    (workspace / ".harness" / "artifacts").symlink_to(private, target_is_directory=True)
    ctx = MagicMock()
    ctx.data_dir = workspace / ".harness" / "tools" / "create_artifact"
    ctx.workspace = workspace

    result = await handle_create_artifact({"name": "secret.md", "content": "overwrite"}, ctx)

    assert result.is_error
    assert (private / "secret.md").read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_artifact_plugins_registration(tmp_path: Path):
    """Both stores.fs_artifacts and tools.artifacts register cleanly with PluginRegistry."""
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings")
    secrets = Secrets(tmp_path / "secrets")

    registry = PluginRegistry(
        workspace=tmp_path / "ws",
        settings=settings,
        secrets=secrets,
        bus=bus,
    )

    builtin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=builtin_dir)
    await registry.load_all()

    assert "artifacts" in registry.stores
    assert "create_artifact" in registry.tools

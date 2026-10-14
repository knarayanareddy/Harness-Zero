"""
Tests for PLG-03: Filesystem Tools plugin (§4, §11, PLG-03).
Uses ToolKit and validates view, write, and search/replace diff editing.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.tools.fs.tools import (
    EDIT_FILE_SPEC,
    VIEW_FILE_SPEC,
    WRITE_FILE_SPEC,
    handle_edit_file,
    handle_view_file,
    handle_write_to_file,
)
from harness.sdk.testing import ToolKit


@pytest.fixture
def mock_ctx(tmp_path: Path):
    ctx = MagicMock()
    ctx.workspace = tmp_path
    return ctx


def test_toolkit_conformance():
    """Validates ToolKit invariants on view_file, write_to_file, edit_file specs."""
    kit = ToolKit()
    for spec in (VIEW_FILE_SPEC, WRITE_FILE_SPEC, EDIT_FILE_SPEC):
        kit.test_input_schema_valid_and_closed(spec)
        kit.test_workspace_path_formatting(spec)
        kit.test_declared_risk_class_bound(spec)

    kit.test_no_direct_subprocess_import(handle_view_file)
    kit.test_no_direct_subprocess_import(handle_write_to_file)
    kit.test_no_direct_subprocess_import(handle_edit_file)


@pytest.mark.asyncio
async def test_view_file_handler(mock_ctx, tmp_path: Path):
    """Verifies view_file reads full file and line ranges."""
    target = tmp_path / "sample.py"
    target.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")

    # Full view
    res = await handle_view_file({"file_path": "sample.py"}, mock_ctx)
    assert not res.is_error
    assert "line 1" in res.content[0].text
    assert "line 5" in res.content[0].text

    # Line range view
    res_range = await handle_view_file({"file_path": "sample.py", "start_line": 2, "end_line": 4}, mock_ctx)
    assert not res_range.is_error
    text = res_range.content[0].text
    assert "line 2" in text
    assert "line 3" in text
    assert "line 1" not in text
    assert "line 5" not in text


@pytest.mark.asyncio
async def test_write_to_file_handler(mock_ctx, tmp_path: Path):
    """Verifies write_to_file creates parent directories and writes content."""
    res = await handle_write_to_file({"file_path": "subdir/nested/test.txt", "content": "Hello World!"}, mock_ctx)
    assert not res.is_error

    written_file = tmp_path / "subdir" / "nested" / "test.txt"
    assert written_file.exists()
    assert written_file.read_text(encoding="utf-8") == "Hello World!"
    assert str(written_file) in res.metadata["changed_files"]


@pytest.mark.asyncio
async def test_edit_file_handler(mock_ctx, tmp_path: Path):
    """Verifies edit_file applies search/replace diff and emits diff card."""
    target = tmp_path / "greeting.py"
    target.write_text("def greet():\n    print('Hello World')\n", encoding="utf-8")

    diff_block = """<<<<<<< SEARCH
def greet():
    print('Hello World')
=======
def greet():
    print('Hello Sovereign Agent')
>>>>>>> REPLACE"""

    res = await handle_edit_file({"file_path": "greeting.py", "diff": diff_block}, mock_ctx)
    assert not res.is_error
    assert len(res.cards) == 1
    assert res.cards[0].kind == "diff"
    assert target.read_text(encoding="utf-8") == "def greet():\n    print('Hello Sovereign Agent')\n"
    assert str(target) in res.metadata["changed_files"]


@pytest.mark.asyncio
async def test_edit_file_search_mismatch(mock_ctx, tmp_path: Path):
    """Verifies edit_file returns error when search block does not match."""
    target = tmp_path / "calc.py"
    target.write_text("x = 10\n", encoding="utf-8")

    diff_block = """<<<<<<< SEARCH
x = 9999
=======
x = 20
>>>>>>> REPLACE"""

    res = await handle_edit_file({"file_path": "calc.py", "diff": diff_block}, mock_ctx)
    assert res.is_error
    assert "Search block not found" in res.content[0].text


@pytest.mark.asyncio
async def test_plugin_discovery_and_setup(tmp_path: Path):
    """Verifies PluginRegistry discovers and registers fs-tools."""
    ws = tmp_path / "ws"
    ws.mkdir()
    conf = tmp_path / "conf"
    conf.mkdir()

    bus = EventBus()
    settings = SettingsStore(path=conf)
    secrets = Secrets(path=conf)
    registry = PluginRegistry(workspace=ws, settings=settings, secrets=secrets, bus=bus)

    builtin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=builtin_dir)
    await registry.load_all()

    assert "fs-tools" in registry.discovered
    assert registry.is_loaded("fs-tools")
    assert "view_file" in registry.tools
    assert "write_to_file" in registry.tools
    assert "edit_file" in registry.tools

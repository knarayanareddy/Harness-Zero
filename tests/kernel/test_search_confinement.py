"""T04: real-gate search must not open repository-controlled host-file links."""
from __future__ import annotations

import builtins
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from harness.kernel.bootstrap import bootstrap_kernel
from harness.plugins.tools.search import tools as search_tools
from harness.sdk.types import ToolCall


@pytest_asyncio.fixture
async def search_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace, config_dir=tmp_path / "config",
        overrides={"kernel.security.preset": "locked"}, resume_session=False,
    )
    try:
        yield runtime
    finally:
        await runtime.registry.unload_all()


async def test_locked_search_does_not_read_outside_file_symlink(search_runtime, tmp_path):
    workspace = search_runtime.registry.workspace
    outside = tmp_path / "outside.txt"
    outside.write_text("MATCH outside-canary")
    (workspace / "linked.txt").symlink_to(outside)
    (workspace / "safe.txt").write_text("MATCH inside-control")

    result = await search_runtime.gate.execute(
        ToolCall(id="search", name="search_codebase", arguments={"query": "MATCH"})
    )

    assert not result.is_error
    assert "safe.txt:1: MATCH inside-control" in result.output
    assert "outside-canary" not in result.output
    assert "linked.txt" not in result.output
    assert result.metadata["count"] == 1


async def search(runtime, **arguments):
    return await runtime.gate.execute(
        ToolCall(id="search", name="search_codebase", arguments={"query": "MATCH", **arguments})
    )


async def test_discovered_symlinks_are_skipped_even_when_their_target_is_inside(search_runtime):
    ws = search_runtime.registry.workspace
    (ws / "safe.txt").write_text("MATCH inside-control")
    (ws / "file-alias.txt").symlink_to(ws / "safe.txt")
    nested = ws / "nested"
    nested.mkdir()
    (nested / "safe.txt").write_text("MATCH nested-control")
    (ws / "dir-alias").symlink_to(nested, target_is_directory=True)
    result = await search(search_runtime)
    assert not result.is_error
    assert "inside-control" in result.output and "nested-control" in result.output
    assert "file-alias" not in result.output and "dir-alias" not in result.output
    assert result.metadata["count"] == 2


async def test_outside_directory_symlink_is_not_descended(search_runtime, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("MATCH outside-canary")
    ws = search_runtime.registry.workspace
    (ws / "alias").symlink_to(outside, target_is_directory=True)
    (ws / "safe.txt").write_text("MATCH inside-control")
    result = await search(search_runtime)
    assert not result.is_error
    assert "inside-control" in result.output
    assert "outside-canary" not in result.output
    assert result.metadata["count"] == 1


@pytest.mark.parametrize("path", ["../outside", "nested/../../outside"])
async def test_gate_rejects_traversal(search_runtime, tmp_path, path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("MATCH outside-canary")
    (search_runtime.registry.workspace / "nested").mkdir()
    result = await search(search_runtime, path=path)
    assert result.is_error
    assert "outside-canary" not in result.output


async def test_subdirectory_search_keeps_gate_validated_inside_alias(search_runtime):
    ws = search_runtime.registry.workspace
    (ws / "top.txt").write_text("MATCH top-control")
    nested = ws / "nested"
    nested.mkdir()
    (nested / "safe.txt").write_text("MATCH nested-control")
    (ws / "alias").symlink_to(nested, target_is_directory=True)
    result = await search(search_runtime, path="nested")
    assert not result.is_error
    assert "nested/safe.txt:1: MATCH nested-control" in result.output
    assert "top-control" not in result.output
    alias = await search(search_runtime, path="alias")
    # The gate canonicalizes explicit inside aliases before calling the handler.
    assert not alias.is_error
    assert "nested/safe.txt:1: MATCH nested-control" in alias.output
    assert "top-control" not in alias.output


def swap_at_open(monkeypatch, candidate, descriptor_name, swap):
    """Intercept both the old pathname read and the descriptor read, without sleeps.

    The wrapper preserves dir_fd support; the supported-function set must name
    the wrapper too. Every race test asserts that this injection really fired.
    """
    original_open, original_builtin = os.open, builtins.open
    fired = []

    def once():
        if not fired:
            fired.append(True)
            swap()

    def descriptor_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == descriptor_name and dir_fd is not None:
            once()
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def pathname_open(path, *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path) == candidate:
            once()
        return original_builtin(path, *args, **kwargs)

    monkeypatch.setattr(search_tools.os, "supports_dir_fd", os.supports_dir_fd | {descriptor_open})
    monkeypatch.setattr(search_tools.os, "open", descriptor_open)
    monkeypatch.setattr(builtins, "open", pathname_open)
    return fired


async def test_leaf_swapped_to_outside_symlink_at_open_is_not_read(search_runtime, tmp_path, monkeypatch):
    ws = search_runtime.registry.workspace
    outside = tmp_path / "secret.txt"
    outside.write_text("MATCH outside-canary")
    candidate = ws / "swap.txt"
    candidate.write_text("MATCH original")
    (ws / "safe.txt").write_text("MATCH inside-control")

    def swap():
        candidate.unlink()
        candidate.symlink_to(outside)

    fired = swap_at_open(monkeypatch, candidate, "swap.txt", swap)
    result = await search(search_runtime)
    assert fired, "setup: the open-time swap was not exercised"
    assert not result.is_error
    assert "inside-control" in result.output
    assert "outside-canary" not in result.output


async def test_parent_swap_after_acquisition_cannot_redirect_read(search_runtime, tmp_path, monkeypatch):
    ws = search_runtime.registry.workspace
    parent = ws / "pkg"
    parent.mkdir()
    candidate = parent / "item.txt"
    candidate.write_text("MATCH original-inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "item.txt").write_text("MATCH outside-canary")

    def swap():
        parent.rename(ws / "saved")
        parent.symlink_to(outside, target_is_directory=True)

    fired = swap_at_open(monkeypatch, candidate, "item.txt", swap)
    result = await search(search_runtime)
    assert fired, "setup: the parent swap was not exercised"
    assert not result.is_error
    assert "outside-canary" not in result.output
    assert "original-inside" in result.output  # The acquired directory stays pinned.


async def test_search_root_swapped_after_gate_validation_is_rejected(search_runtime, tmp_path, monkeypatch):
    ws = search_runtime.registry.workspace
    parent = ws / "pkg"
    parent.mkdir()
    candidate = parent / "item.txt"
    candidate.write_text("MATCH original-inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "item.txt").write_text("MATCH outside-canary")

    def swap():
        parent.rename(ws / "saved")
        parent.symlink_to(outside, target_is_directory=True)

    fired = swap_at_open(monkeypatch, candidate, "pkg", swap)
    result = await search(search_runtime, path="pkg")
    assert fired, "setup: the root swap was not exercised"
    assert result.is_error
    assert "outside-canary" not in result.output


async def test_ignore_glob_and_pagination_remain_bounded(search_runtime):
    ws = search_runtime.registry.workspace
    for name in ("a.txt", "b.txt", "c.txt", "other.py"):
        (ws / name).write_text("MATCH inside-control")
    for directory in (".git", ".harness", "node_modules", ".venv"):
        ignored = ws / directory
        ignored.mkdir(exist_ok=True)
        (ignored / "hidden.txt").write_text("MATCH ignored-canary")
    result = await search(search_runtime, glob_filter="*.txt", offset=1, max_results=1)
    assert not result.is_error
    assert "inside-control" in result.output
    assert "ignored-canary" not in result.output
    assert "other.py" not in result.output
    assert result.metadata["count"] == 1
    assert result.metadata["has_more"] is True
    excluded = await search(search_runtime, glob_filter="!*.txt")
    assert not excluded.is_error
    assert "other.py" in excluded.output
    assert "ignored-canary" not in excluded.output
    assert excluded.metadata["count"] == 1


@pytest.mark.parametrize("relative_root", [".harness", "nested/node_modules"])
async def test_explicit_ignored_root_cannot_bypass_ignore_policy(search_runtime, relative_root):
    ignored = search_runtime.registry.workspace / relative_root
    ignored.mkdir(parents=True, exist_ok=True)
    (ignored / "private.txt").write_text("MATCH ignored-root-canary")
    result = await search(search_runtime, path=relative_root)
    assert not result.is_error
    assert "ignored-root-canary" not in result.output
    assert result.metadata["count"] == 0


async def test_unreadable_entry_does_not_hide_readable_siblings(search_runtime, monkeypatch):
    ws = search_runtime.registry.workspace
    (ws / "unreadable.txt").write_text("MATCH inaccessible")
    (ws / "safe.txt").write_text("MATCH inside-control")
    original = os.open

    def reject_one(path, flags, mode=0o777, *, dir_fd=None):
        if path == "unreadable.txt" and dir_fd is not None:
            raise PermissionError("synthetic denied entry")
        return original(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(search_tools.os, "supports_dir_fd", os.supports_dir_fd | {reject_one})
    monkeypatch.setattr(search_tools.os, "open", reject_one)
    result = await search(search_runtime)
    assert not result.is_error
    assert "inside-control" in result.output
    assert "inaccessible" not in result.output


def test_limit_return_closes_every_acquired_descriptor(tmp_path, monkeypatch):
    ws = tmp_path.resolve()
    nested = ws / "nested"
    nested.mkdir()
    (nested / "safe.txt").write_text("MATCH first\nMATCH second\n")
    original_open, original_close = os.open, os.close
    acquired = set()

    def tracking_open(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        acquired.add(fd)
        return fd

    def tracking_close(fd):
        original_close(fd)
        acquired.discard(fd)

    monkeypatch.setattr(search_tools.os, "supports_dir_fd", os.supports_dir_fd | {tracking_open})
    monkeypatch.setattr(search_tools.os, "open", tracking_open)
    monkeypatch.setattr(search_tools.os, "close", tracking_close)
    for _ in range(20):
        result = search_tools._search_python(ws, nested, re.compile("MATCH"), None, 1)
        assert len(result) == 1
        assert not acquired, "Early pagination return leaked descriptors"


def test_fifo_is_not_read_or_allowed_to_block_search(tmp_path):
    (tmp_path / "safe.txt").write_text("MATCH inside-control")
    os.mkfifo(tmp_path / "pipe")
    # A separate process makes a missing O_NONBLOCK regression bounded: an event
    # loop timeout cannot interrupt an accidental blocking open in Python.
    code = (
        "import sys,re; from pathlib import Path; "
        "from harness.plugins.tools.search.tools import _search_python; "
        "ws=Path(sys.argv[1]).resolve(); "
        "print(_search_python(ws,ws,re.compile('MATCH'),None,5))"
    )
    process = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=5, check=True)
    assert "inside-control" in process.stdout
    assert "pipe" not in process.stdout

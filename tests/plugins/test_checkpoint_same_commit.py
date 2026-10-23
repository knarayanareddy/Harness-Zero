"""G1: unchanged immutable checkpoint endpoints need validation, not subprocess diffs."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness.plugins.hooks.checkpoints.changes import compute_changes


@pytest.mark.parametrize("exists", [True, False])
async def test_same_checkpoint_validates_once_without_diff_subprocesses(tmp_path, monkeypatch, exists):
    checked = []
    sha = "a" * 40

    async def has_commit(value):
        checked.append(value)
        return exists

    async def unexpected_diff(*args, **kwargs):
        pytest.fail("Identical validated commits must not launch redundant Git diff processes")

    monkeypatch.setattr("harness.plugins.hooks.checkpoints.changes._run_git", unexpected_diff)
    repo = SimpleNamespace(has_commit=has_commit, git_dir=tmp_path / ".git", workspace=tmp_path)
    if exists:
        assert await compute_changes(repo, sha, sha) == {
            "base": sha, "head": sha, "files": [], "added": 0, "removed": 0,
            "truncated": False, "total_files": 0,
        }
    else:
        with pytest.raises(ValueError, match="must be commits"):
            await compute_changes(repo, sha, sha)
    assert checked == [sha]

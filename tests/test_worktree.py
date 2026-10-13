from pathlib import Path
import subprocess
import pytest
from harness.sandboxing.worktree import WorktreeManager


def test_worktree_lifecycle(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    # Initialize a git repo with an initial commit
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test Agent"], check=True)

    dummy_file = repo / "README.md"
    dummy_file.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "Initial commit"], check=True)

    # Test worktree creation
    wt_manager = WorktreeManager(repo_root=repo)
    worker_path = wt_manager.create_worktree("agent_42")
    assert worker_path.exists()
    assert (worker_path / "README.md").exists()

    # Worker modifies file in its isolated worktree
    (worker_path / "README.md").write_text("# Test Repo Modified\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(worker_path), "commit", "-am", "Worker edit"], check=True)

    # Check diff
    diff = wt_manager.get_diff("agent_42", base_branch="main")
    assert "Modified" in diff

    # Teardown
    wt_manager.remove_worktree("agent_42")
    assert not worker_path.exists()

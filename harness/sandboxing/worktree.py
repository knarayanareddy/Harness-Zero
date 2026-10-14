"""
Subagent Git Worktree Sandboxing.
Provisions isolated worktrees to prevent parallel agent file editing collisions.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class WorktreeManager:
    def __init__(self, repo_root: Path, worktree_base_dir: Path | None = None):
        self.repo_root = Path(repo_root)
        self.worktree_base = (
            Path(worktree_base_dir)
            if worktree_base_dir
            else self.repo_root / ".harness" / "worktrees"
        )
        self.worktree_base.mkdir(parents=True, exist_ok=True)
        self._active_worktrees: dict[str, Path] = {}

    def create_worktree(self, subagent_id: str, base_branch: str = "HEAD") -> Path:
        target_dir = self.worktree_base / f"worker_{subagent_id}"
        branch_name = f"harness_worker_{subagent_id}"

        if target_dir.exists():
            self.remove_worktree(subagent_id)

        # git worktree add <dir> -b <branch> <base>
        cmd = [
            "git",
            "-C",
            str(self.repo_root),
            "worktree",
            "add",
            str(target_dir),
            "-b",
            branch_name,
            base_branch,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to create git worktree at {target_dir}: {res.stderr}"
            )

        self._active_worktrees[subagent_id] = target_dir
        return target_dir

    def get_diff(self, subagent_id: str, base_branch: str = "main") -> str:
        """Returns diff between the subagent branch and base branch."""
        branch_name = f"harness_worker_{subagent_id}"
        cmd = ["git", "-C", str(self.repo_root), "diff", f"{base_branch}..{branch_name}"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.stdout

    def remove_worktree(self, subagent_id: str, delete_branch: bool = True) -> None:
        target_dir = self._active_worktrees.get(subagent_id)
        if not target_dir:
            target_dir = self.worktree_base / f"worker_{subagent_id}"

        if target_dir.exists():
            cmd = [
                "git",
                "-C",
                str(self.repo_root),
                "worktree",
                "remove",
                "--force",
                str(target_dir),
            ]
            subprocess.run(cmd, capture_output=True, text=True)
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)

        if delete_branch:
            branch_name = f"harness_worker_{subagent_id}"
            subprocess.run(
                ["git", "-C", str(self.repo_root), "branch", "-D", branch_name],
                capture_output=True,
            )

        self._active_worktrees.pop(subagent_id, None)

    def prune(self) -> None:
        """Prunes stale worktree metadata in git."""
        subprocess.run(["git", "-C", str(self.repo_root), "worktree", "prune"], capture_output=True)

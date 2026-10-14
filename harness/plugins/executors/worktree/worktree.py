"""
Async Git Worktree Sandboxing (§PLG-18).
Absorbs ORCH-03.
Provisions isolated worktrees with base commit recording, stale branch cleanup,
and non-blocking async git execution.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


async def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Runs a git command asynchronously and returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


class WorktreeManager:
    """Manages isolated git worktrees for subagents with async operations."""

    def __init__(self, repo_root: Path, worktree_base_dir: Path | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.worktree_base = (
            Path(worktree_base_dir)
            if worktree_base_dir
            else self.repo_root / ".harness" / "worktrees"
        )
        self.worktree_base.mkdir(parents=True, exist_ok=True)
        self._active_worktrees: dict[str, tuple[Path, str]] = {}

    async def get_head_commit(self) -> str:
        """Returns the current HEAD commit hash of the main repository."""
        code, out, _ = await _run_git(["rev-parse", "HEAD"], self.repo_root)
        if code != 0 or not out.strip():
            return "HEAD"
        return out.strip()

    async def prune(self) -> None:
        """Prunes stale worktree administrative files."""
        await _run_git(["worktree", "prune"], self.repo_root)

    async def create_worktree(
        self, subagent_id: str, base_branch: str | None = None
    ) -> tuple[Path, str]:
        """
        Creates an isolated worktree for a subagent.
        Records base commit hash and handles stale branches and directories.
        """
        target_dir = self.worktree_base / f"worker_{subagent_id}"
        branch_name = f"harness_worker_{subagent_id}"

        # 1. Prune stale worktrees first
        await self.prune()

        # 2. Cleanup existing worktree if already tracked
        if subagent_id in self._active_worktrees or target_dir.exists():
            await self.remove_worktree(subagent_id, delete_branch=True)

        # 3. Clean up stale branch with same name if it exists
        code, out, _ = await _run_git(["branch", "--list", branch_name], self.repo_root)
        if branch_name in out:
            await _run_git(["branch", "-D", branch_name], self.repo_root)

        # 4. Resolve base commit
        base_commit = base_branch or await self.get_head_commit()

        # 5. Create worktree
        cmd = [
            "worktree",
            "add",
            str(target_dir),
            "-b",
            branch_name,
            base_commit,
        ]
        code, out, err = await _run_git(cmd, self.repo_root)
        if code != 0:
            raise RuntimeError(
                f"Failed to create git worktree at {target_dir}: {err.strip() or out.strip()}"
            )

        self._active_worktrees[subagent_id] = (target_dir, base_commit)
        return target_dir, base_commit

    async def get_diff(self, subagent_id: str, base_commit: str | None = None) -> str:
        """Returns diff between recorded base commit and subagent branch."""
        recorded_commit = "HEAD"
        if subagent_id in self._active_worktrees:
            recorded_commit = self._active_worktrees[subagent_id][1]

        base = base_commit or recorded_commit
        branch_name = f"harness_worker_{subagent_id}"

        code, out, _ = await _run_git(["diff", f"{base}..{branch_name}"], self.repo_root)
        return out

    async def remove_worktree(self, subagent_id: str, delete_branch: bool = True) -> None:
        """Removes a subagent worktree and optionally deletes its branch."""
        entry = self._active_worktrees.get(subagent_id)
        target_dir = entry[0] if entry else self.worktree_base / f"worker_{subagent_id}"
        branch_name = f"harness_worker_{subagent_id}"

        if target_dir.exists():
            await _run_git(["worktree", "remove", "--force", str(target_dir)], self.repo_root)
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)

        if delete_branch:
            await _run_git(["branch", "-D", branch_name], self.repo_root)

        await self.prune()
        self._active_worktrees.pop(subagent_id, None)

"""
Shadow Git Repository for Cline-style Checkpoints (§PLG-22).
Maintains shadow git repository at <workspace>/.harness/checkpoints/.git to take
lightweight atomic snapshots of workspace state before mutating tools.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


async def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


class ShadowRepo:
    """Manages shadow git commits and restores for a workspace."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.checkpoints_dir = self.workspace / ".harness" / "checkpoints"
        self.git_dir = self.checkpoints_dir / ".git"

    async def init_if_needed(self) -> None:
        """Initializes shadow git repository if not already present."""
        if not self.git_dir.exists():
            self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
            await _run_git(["init", "--bare", str(self.git_dir)], self.workspace)
            await _run_git(["--git-dir", str(self.git_dir), "config", "user.name", "Harness Checkpoint"], self.workspace)
            await _run_git(["--git-dir", str(self.git_dir), "config", "user.email", "checkpoints@harness.internal"], self.workspace)
            # Setup info/exclude to ignore .harness
            info_dir = self.git_dir / "info"
            info_dir.mkdir(parents=True, exist_ok=True)
            exclude_file = info_dir / "exclude"
            exclude_file.write_text(".harness/\n", encoding="utf-8")

    async def snapshot(self, label: str) -> tuple[str, str | None]:
        """
        Takes a snapshot of workspace files.
        Returns (commit_sha, diff_against_previous_or_None).
        """
        await self.init_if_needed()

        # Add all workspace files
        await _run_git(
            ["--git-dir", str(self.git_dir), "--work-tree", str(self.workspace), "add", "-A"],
            self.workspace,
        )

        # Check if there are changes compared to HEAD
        code, head_sha, _ = await _run_git(
            ["--git-dir", str(self.git_dir), "rev-parse", "HEAD"],
            self.workspace,
        )
        has_head = (code == 0 and bool(head_sha.strip()))

        if has_head:
            code_diff, _, _ = await _run_git(
                ["--git-dir", str(self.git_dir), "--work-tree", str(self.workspace), "diff-index", "--quiet", "HEAD", "--"],
                self.workspace,
            )
            # 0 means clean/no changes
            if code_diff == 0:
                return head_sha.strip(), None

        # Commit changes
        code, _, err = await _run_git(
            [
                "--git-dir", str(self.git_dir),
                "--work-tree", str(self.workspace),
                "commit",
                "-m", label,
                "--allow-empty",
            ],
            self.workspace,
        )
        if code != 0:
            raise RuntimeError(f"Shadow commit failed: {err}")

        # Get new HEAD sha
        _, new_sha, _ = await _run_git(
            ["--git-dir", str(self.git_dir), "rev-parse", "HEAD"],
            self.workspace,
        )
        commit_sha = new_sha.strip()

        # Compute diff if previous commit exists
        diff_text: str | None = None
        if has_head:
            _, diff_out, _ = await _run_git(
                ["--git-dir", str(self.git_dir), "diff", f"{head_sha.strip()}..{commit_sha}"],
                self.workspace,
            )
            if diff_out.strip():
                diff_text = diff_out

        return commit_sha, diff_text

    async def restore(self, commit_sha: str) -> str:
        """Restores workspace to the state of a given shadow commit."""
        await self.init_if_needed()

        code, _, err = await _run_git(
            [
                "--git-dir", str(self.git_dir),
                "--work-tree", str(self.workspace),
                "reset", "--hard", commit_sha,
            ],
            self.workspace,
        )
        if code != 0:
            raise RuntimeError(f"Shadow reset failed: {err}")

        # Clean any files untracked in that commit
        await _run_git(
            [
                "--git-dir", str(self.git_dir),
                "--work-tree", str(self.workspace),
                "clean", "-fd",
            ],
            self.workspace,
        )
        return f"Restored workspace to checkpoint '{commit_sha}'."

    async def list_checkpoints(self, limit: int = 20) -> list[dict[str, str]]:
        """Returns list of recent shadow commits."""
        await self.init_if_needed()
        code, out, _ = await _run_git(
            [
                "--git-dir", str(self.git_dir),
                "log", f"-n{limit}", "--pretty=format:%H|%s|%ct",
            ],
            self.workspace,
        )
        if code != 0 or not out.strip():
            return []

        checkpoints = []
        for line in out.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 3:
                checkpoints.append({
                    "sha": parts[0],
                    "label": parts[1],
                    "timestamp": parts[2],
                })
        return checkpoints

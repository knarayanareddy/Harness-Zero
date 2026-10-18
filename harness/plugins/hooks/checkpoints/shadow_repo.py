"""Private shadow Git snapshots for the workspace (never the user's Git repository)."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


async def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run Git without a shell and decode diagnostics for callers."""
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


@dataclass(frozen=True)
class SnapshotResult:
    sha: str | None
    diff: str | None
    created: bool
    skipped_reason: str | None = None
    workspace_bytes: int = 0


class ShadowRepo:
    """Manage isolated commits in ``<workspace>/.harness/checkpoints/.git``."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.checkpoints_dir = self.workspace / ".harness" / "checkpoints"
        self.git_dir = self.checkpoints_dir / ".git"

    def _prefix(self) -> list[str]:
        return ["--git-dir", str(self.git_dir), "--work-tree", str(self.workspace)]

    async def init_if_needed(self) -> None:
        """Initialize the private bare repo and exclude all harness/user Git metadata."""
        if not self.git_dir.exists():
            self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
            code, _, err = await _run_git(["init", "--bare", str(self.git_dir)], self.workspace)
            if code != 0:
                raise RuntimeError(f"Shadow repository init failed: {err}")
            await _run_git(["--git-dir", str(self.git_dir), "config", "user.name", "Harness Checkpoint"], self.workspace)
            await _run_git(["--git-dir", str(self.git_dir), "config", "user.email", "checkpoints@harness.internal"], self.workspace)
            info_dir = self.git_dir / "info"
            info_dir.mkdir(parents=True, exist_ok=True)
            # The shadow repo must neither capture nor mutate the user's .git directory or its own storage.
            (info_dir / "exclude").write_text(".harness/\n.git/\n", encoding="utf-8")

    async def head(self) -> str | None:
        """Return the current shadow HEAD, or ``None`` before the first commit."""
        await self.init_if_needed()
        code, out, _ = await _run_git(["--git-dir", str(self.git_dir), "rev-parse", "--verify", "HEAD"], self.workspace)
        return out.strip() if code == 0 and _SHA_RE.fullmatch(out.strip()) else None

    async def _workspace_size(self, max_bytes: int | None) -> tuple[int, bool]:
        """Count only files Git would snapshot; ignored state and symlink targets are not traversed."""
        code, out, err = await _run_git(
            [*self._prefix(), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            self.workspace,
        )
        if code != 0:
            raise RuntimeError(f"Could not enumerate workspace files for checkpointing: {err}")
        total = 0
        for raw in out.split("\0"):
            if not raw:
                continue
            candidate = self.workspace / raw
            try:
                size = candidate.lstat().st_size
            except (FileNotFoundError, OSError):
                continue
            total += size
            if max_bytes is not None and total > max_bytes:
                return total, True
        return total, False

    async def snapshot_with_status(
        self,
        label: str,
        *,
        max_bytes: int | None = None,
    ) -> SnapshotResult:
        """Take a snapshot, reporting whether it created a commit or was skipped by the size guard."""
        await self.init_if_needed()
        workspace_bytes, too_large = await self._workspace_size(max_bytes)
        if too_large:
            return SnapshotResult(
                sha=None,
                diff=None,
                created=False,
                skipped_reason=f"workspace exceeds the {max_bytes}-byte checkpoint limit",
                workspace_bytes=workspace_bytes,
            )

        previous = await self.head()
        code, _, err = await _run_git([*self._prefix(), "add", "-A", "--", "."], self.workspace)
        if code != 0:
            raise RuntimeError(f"Shadow snapshot add failed: {err}")

        if previous is not None:
            code, _, err = await _run_git(
                [*self._prefix(), "diff-index", "--quiet", "HEAD", "--"], self.workspace
            )
            if code == 0:
                return SnapshotResult(previous, None, False, workspace_bytes=workspace_bytes)
            if code != 1:
                raise RuntimeError(f"Could not compare shadow workspace with HEAD: {err}")

        code, _, err = await _run_git(
            [
                *self._prefix(),
                "commit",
                "-m",
                str(label)[:200],
                "--allow-empty",
            ],
            self.workspace,
        )
        if code != 0:
            raise RuntimeError(f"Shadow commit failed: {err}")

        code, out, err = await _run_git(
            ["--git-dir", str(self.git_dir), "rev-parse", "--verify", "HEAD"], self.workspace
        )
        if code != 0 or not _SHA_RE.fullmatch(out.strip()):
            raise RuntimeError(f"Could not read shadow HEAD after snapshot: {err or out}")
        commit_sha = out.strip()

        diff_text: str | None = None
        if previous is not None:
            code, diff_text, err = await _run_git(
                ["--git-dir", str(self.git_dir), "diff", "--no-ext-diff", "--no-textconv", previous, commit_sha],
                self.workspace,
            )
            if code != 0:
                raise RuntimeError(f"Could not compute checkpoint diff: {err}")
            if not diff_text.strip():
                diff_text = None

        return SnapshotResult(
            sha=commit_sha,
            diff=diff_text,
            created=True,
            workspace_bytes=workspace_bytes,
        )

    async def snapshot(
        self,
        label: str,
        *,
        max_bytes: int | None = None,
    ) -> tuple[str | None, str | None]:
        """Compatibility helper returning ``(commit_sha, diff_against_previous_or_None)``."""
        result = await self.snapshot_with_status(label, max_bytes=max_bytes)
        return result.sha, result.diff

    async def has_commit(self, commit_sha: str) -> bool:
        """Return whether a full SHA names a commit in this repo; reject refs and option-like input."""
        if not _SHA_RE.fullmatch(str(commit_sha)):
            return False
        await self.init_if_needed()
        code, _, _ = await _run_git(
            ["--git-dir", str(self.git_dir), "cat-file", "-e", f"{commit_sha}^{{commit}}"],
            self.workspace,
        )
        return code == 0

    async def restore(self, commit_sha: str) -> str:
        """Restore only to a verified commit SHA in this shadow repo."""
        if not _SHA_RE.fullmatch(str(commit_sha)):
            raise ValueError("Checkpoint must be a full 40-character commit SHA")
        if not await self.has_commit(commit_sha):
            raise ValueError("Checkpoint does not exist in this workspace's shadow repository")

        code, _, err = await _run_git(
            [*self._prefix(), "reset", "--hard", commit_sha], self.workspace
        )
        if code != 0:
            raise RuntimeError(f"Shadow reset failed: {err}")

        code, _, err = await _run_git(
            [*self._prefix(), "clean", "-fd", "--"], self.workspace
        )
        if code != 0:
            raise RuntimeError(f"Shadow clean failed after reset: {err}")
        return f"Restored workspace to checkpoint '{commit_sha}'."

    async def list_checkpoints(self, limit: int = 20) -> list[dict[str, str]]:
        """Return recent shadow commits without exposing or reading the user's Git history."""
        await self.init_if_needed()
        n = max(1, min(int(limit), 1000))
        code, out, _ = await _run_git(
            ["--git-dir", str(self.git_dir), "log", f"-n{n}", "--pretty=format:%H|%s|%ct"],
            self.workspace,
        )
        if code != 0 or not out.strip():
            return []

        checkpoints: list[dict[str, str]] = []
        for line in out.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3 and _SHA_RE.fullmatch(parts[0]):
                checkpoints.append({"sha": parts[0], "label": parts[1], "timestamp": parts[2]})
        return checkpoints

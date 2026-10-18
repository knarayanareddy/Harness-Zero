"""Workspace diff helpers for the private checkpoint repository."""
from __future__ import annotations

import asyncio
import re
from pathlib import PurePosixPath
from typing import Any

from .shadow_repo import ShadowRepo, _run_git

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def validate_checkpoint_sha(value: str) -> str:
    """Accept only a full hexadecimal commit id, never a ref or command-line option."""
    sha = str(value or "")
    if not _SHA_RE.fullmatch(sha):
        raise ValueError("Checkpoint SHA must be exactly 40 lowercase hexadecimal characters")
    return sha


def validate_diff_path(value: str) -> str:
    """Validate a Git pathspec as a relative workspace path before passing it after `--`."""
    path = str(value or "")
    if not path or "\0" in path or "\\" in path:
        raise ValueError("Diff path must be a non-empty workspace-relative path")
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in {"", ".", ".."} for part in p.parts):
        raise ValueError("Diff path must not be absolute or contain '.' or '..' segments")
    if p.parts[0] in {".git", ".harness"}:
        raise ValueError("Diff path may not address Git or Harness internal state")
    return p.as_posix()


def _line_count(value: str) -> int | None:
    return None if value == "-" else int(value)


def parse_numstat(output: str) -> dict[str, dict[str, Any]]:
    """Parse ``git diff --numstat -z``, including binary files and rename path pairs."""
    tokens = output.split("\0")
    rows: dict[str, dict[str, Any]] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token:
            i += 1
            continue
        columns = token.split("\t", 2)
        if len(columns) != 3:
            raise ValueError(f"Unexpected git numstat row: {token!r}")
        added = _line_count(columns[0])
        removed = _line_count(columns[1])
        path = columns[2]
        old_path = None
        i += 1
        if not path:
            if i + 1 >= len(tokens):
                raise ValueError("Truncated rename path in git numstat output")
            old_path, path = tokens[i], tokens[i + 1]
            i += 2
        if not path:
            raise ValueError("Empty path in git numstat output")
        row: dict[str, Any] = {
            "path": path,
            "added_lines": added,
            "removed_lines": removed,
            "binary": added is None or removed is None,
        }
        if old_path:
            row["old_path"] = old_path
        rows[path] = row
    return rows


def parse_name_status(output: str) -> list[dict[str, Any]]:
    """Parse ``git diff --name-status -M -z`` without splitting filenames on whitespace."""
    tokens = output.split("\0")
    rows: list[dict[str, Any]] = []
    i = 0
    while i < len(tokens):
        code = tokens[i]
        if not code:
            i += 1
            continue
        i += 1
        if code[0] in {"R", "C"}:
            if i + 1 >= len(tokens):
                raise ValueError("Truncated rename path in git name-status output")
            old_path, path = tokens[i], tokens[i + 1]
            i += 2
            rows.append({"path": path, "old_path": old_path, "status": "renamed"})
        else:
            if i >= len(tokens):
                raise ValueError("Truncated path in git name-status output")
            path = tokens[i]
            i += 1
            status = {"A": "added", "M": "modified", "D": "deleted", "T": "modified"}.get(code[0], "modified")
            rows.append({"path": path, "status": status})
    return rows


async def compute_changes(
    repo: ShadowRepo,
    base: str,
    head: str,
    *,
    max_files: int = 200,
) -> dict[str, Any]:
    """Return a bounded file summary and exact line totals for the entire checkpoint delta."""
    base_sha = validate_checkpoint_sha(base)
    head_sha = validate_checkpoint_sha(head)
    limit = max(0, int(max_files))
    if not await repo.has_commit(base_sha) or not await repo.has_commit(head_sha):
        raise ValueError("Diff endpoints must be commits in this workspace's shadow repository")

    git_prefix = ["--git-dir", str(repo.git_dir)]
    numstat_code, numstat_out, numstat_err = await _run_git(
        [*git_prefix, "diff", "--numstat", "--find-renames", "-z", base_sha, head_sha, "--"],
        repo.workspace,
    )
    if numstat_code != 0:
        raise RuntimeError(f"Could not compute workspace numstat: {numstat_err}")
    status_code, status_out, status_err = await _run_git(
        [*git_prefix, "diff", "--name-status", "--find-renames", "-z", base_sha, head_sha, "--"],
        repo.workspace,
    )
    if status_code != 0:
        raise RuntimeError(f"Could not compute workspace statuses: {status_err}")

    stats = parse_numstat(numstat_out)
    status_rows = parse_name_status(status_out)
    files: list[dict[str, Any]] = []
    for status in status_rows:
        row = {**status, **stats.get(status["path"], {})}
        row.setdefault("added_lines", 0)
        row.setdefault("removed_lines", 0)
        row.setdefault("binary", False)
        files.append(row)

    added = sum(row["added_lines"] or 0 for row in files)
    removed = sum(row["removed_lines"] or 0 for row in files)
    truncated = len(files) > limit
    return {
        "base": base_sha,
        "head": head_sha,
        "files": files[:limit],
        "added": added,
        "removed": removed,
        "truncated": truncated,
        "total_files": len(files),
    }


async def changed_file_count(repo: ShadowRepo, base: str, head: str) -> int:
    """Return the number of paths a restore changes, including binary files and renames."""
    base_sha = validate_checkpoint_sha(base)
    head_sha = validate_checkpoint_sha(head)
    code, output, err = await _run_git(
        ["--git-dir", str(repo.git_dir), "diff", "--name-status", "--find-renames", "-z", base_sha, head_sha, "--"],
        repo.workspace,
    )
    if code != 0:
        raise RuntimeError(f"Could not count files for restore: {err}")
    return len(parse_name_status(output))


async def diff_file(
    repo: ShadowRepo,
    base: str,
    head: str,
    path: str,
    *,
    max_bytes: int = 262_144,
) -> dict[str, Any]:
    """Return one textual diff, retaining no more than ``max_bytes`` of Git output in memory."""
    base_sha = validate_checkpoint_sha(base)
    head_sha = validate_checkpoint_sha(head)
    safe_path = validate_diff_path(path)
    if not await repo.has_commit(base_sha) or not await repo.has_commit(head_sha):
        raise ValueError("Diff endpoints must be commits in this workspace's shadow repository")
    cap = max(1, int(max_bytes))

    # Determine binary status before requesting a potentially large patch.
    code, numstat, err = await _run_git(
        ["--git-dir", str(repo.git_dir), "diff", "--numstat", "-z", base_sha, head_sha, "--", safe_path],
        repo.workspace,
    )
    if code != 0:
        raise RuntimeError(f"Could not inspect diff: {err}")
    stats = parse_numstat(numstat)
    stat = stats.get(safe_path, {})
    if stat.get("binary"):
        return {"path": safe_path, "base": base_sha, "head": head_sha, "diff": "", "binary": True, "truncated": False}

    proc = await asyncio.create_subprocess_exec(
        "git",
        "--git-dir",
        str(repo.git_dir),
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--unified=3",
        base_sha,
        head_sha,
        "--",
        safe_path,
        cwd=repo.workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if proc.stdout is None or proc.stderr is None:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Could not read Git diff output")

    async def read_limited(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
        output = bytearray()
        truncated = False
        while chunk := await stream.read(16_384):
            remaining = limit + 1 - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(chunk) > remaining or len(output) > limit:
                truncated = True
        return bytes(output[:limit]), truncated

    stdout_task = asyncio.create_task(read_limited(proc.stdout, cap))
    stderr_task = asyncio.create_task(proc.stderr.read())
    await proc.wait()
    output, truncated = await stdout_task
    stderr = (await stderr_task).decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"Git diff failed: {stderr}")
    text = output.decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[diff truncated at {cap} bytes]"
    return {
        "path": safe_path,
        "base": base_sha,
        "head": head_sha,
        "diff": text,
        "binary": False,
        "truncated": truncated,
        "added_lines": stat.get("added_lines"),
        "removed_lines": stat.get("removed_lines"),
    }

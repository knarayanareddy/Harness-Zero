"""
Codebase search tool handler (§6.6, PLG-05).
Absorbs HYG-03.
"""
from __future__ import annotations

import fnmatch
import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from harness.sdk.context import ToolContext
from harness.sdk.types import RiskClass, ToolResult, ToolSpec

SEARCH_CODEBASE_SPEC = ToolSpec(
    name="search_codebase",
    description="Searches regular workspace files using regex matching without following symlinks.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Regex pattern to search for in codebase.",
            },
            "Query": {
                "type": "string",
                "description": "Alternative parameter for query.",
            },
            "path": {
                "type": "string",
                "format": "workspace-path",
                "description": "Subdirectory to restrict search within workspace.",
            },
            "glob_filter": {
                "type": "string",
                "description": "Optional glob filter pattern (e.g. '*.py' or '!*.min.js').",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Whether regex search is case-sensitive (default: false).",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Maximum number of search results to return (default: 50).",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Offset index for paginating results (default: 0).",
            },
        },
        "additionalProperties": False,
    },
    risk_class=RiskClass.READ,
    capabilities=frozenset(["fs:read"]),
    timeout_s=60.0,
)

IGNORE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".harness",
}


def _search_python(
    workspace: Path,
    root: Path,
    query_pattern: re.Pattern[str],
    glob_pattern: str | None,
    limit: int,
) -> list[tuple[str, int, str]]:
    """Walk and open relative to pinned directory descriptors, never following links.

    A resolve/check followed by a pathname open is insufficient: the repository
    can swap a leaf or parent between them. Each child is opened relative to its
    already-open parent with O_NOFOLLOW. Directory replacement cannot redirect
    an acquired descriptor. Search deliberately skips even inside-workspace
    symlinks; other filesystem tools retain their own policies.
    """
    if os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("This platform does not support descriptor-confined search")
    results: list[tuple[str, int, str]] = []
    relative_root = root.relative_to(workspace)
    if ".." in relative_root.parts:
        raise ValueError("Search root must not traverse parent directories")
    if any(part in IGNORE_DIRS for part in relative_root.parts):
        return results  # An explicit subdirectory must not bypass ignored trees.
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    child_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

    def visit(directory_fd: int, relative_dir: Path) -> bool:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if entry.name in IGNORE_DIRS:
                    continue
                try:
                    fd = os.open(entry.name, child_flags, dir_fd=directory_fd)
                except OSError:
                    continue  # Links, vanished entries and unreadable files are not searched.
                try:
                    relative_file = relative_dir / entry.name
                    mode = os.fstat(fd).st_mode
                    if stat.S_ISDIR(mode):
                        if visit(fd, relative_file):
                            return True
                    elif stat.S_ISREG(mode):
                        if glob_pattern:
                            excluded = glob_pattern.startswith("!")
                            pattern = glob_pattern[1:] if excluded else glob_pattern
                            if fnmatch.fnmatch(entry.name, pattern) == excluded:
                                continue
                        with os.fdopen(fd, encoding="utf-8", errors="replace", closefd=False) as file:
                            for idx, line in enumerate(file, 1):
                                if query_pattern.search(line):
                                    results.append((str(relative_file), idx, line.rstrip("\r\n")))
                                    if len(results) >= limit:
                                        return True
                except OSError:
                    continue
                finally:
                    os.close(fd)
        return False

    with ExitStack() as stack:
        directory_fd = os.open(workspace, directory_flags)
        stack.callback(os.close, directory_fd)
        for part in relative_root.parts:
            directory_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            stack.callback(os.close, directory_fd)
        visit(directory_fd, relative_root)
    return results


async def handle_search_codebase(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = args.get("query") or args.get("Query", "")
    if not query:
        return ToolResult.error("Error: query argument is required")

    case_sensitive = bool(args.get("case_sensitive", False))
    flags = re.MULTILINE if case_sensitive else (re.MULTILINE | re.IGNORECASE)

    try:
        query_pattern = re.compile(query, flags)
    except re.error as err:
        return ToolResult.error(f"Invalid regex pattern: {err}")

    subpath = args.get("path")
    if subpath:
        search_root = ctx.workspace / subpath
    else:
        search_root = ctx.workspace

    glob_filter = args.get("glob_filter")
    max_results = min(500, max(1, int(args.get("max_results", 50))))
    offset = max(0, int(args.get("offset", 0)))
    total_needed = offset + max_results
    fetch_limit = min(1000, total_needed + 1)

    # The read-only tool always uses the same confined reader. An executor/ripgrep
    # path would reopen mutable pathnames outside this descriptor boundary.
    try:
        matches = _search_python(
            workspace=ctx.workspace,
            root=search_root,
            query_pattern=query_pattern,
            glob_pattern=glob_filter,
            limit=fetch_limit,
        )
    except (OSError, ValueError) as err:
        return ToolResult.error(f"Error: search path is not an accessible confined directory: {err}")

    has_more = len(matches) > total_needed
    paginated = matches[offset : total_needed]
    displayed_total = len(matches) if not has_more else total_needed

    if not paginated:
        msg = f"No matches found for query '{query}'"
        if offset > 0:
            msg += f" at offset {offset}"
        return ToolResult.text(msg, count=0, total=0, offset=offset)

    output_lines = [
        f"Found {displayed_total}{'+' if has_more else ''} match(es) (showing {len(paginated)} from offset {offset}):"
    ]
    for rel_path, line_no, content in paginated:
        output_lines.append(f"{rel_path}:{line_no}: {content.strip()}")

    return ToolResult.text(
        "\n".join(output_lines),
        count=len(paginated),
        total=displayed_total,
        offset=offset,
        has_more=has_more,
    )

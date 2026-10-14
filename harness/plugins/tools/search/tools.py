"""
Codebase search tool handler (§6.6, PLG-05).
Absorbs HYG-03.
"""
from __future__ import annotations

import fnmatch
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from harness.sdk.context import ToolContext
from harness.sdk.types import Capability, ExecRequest, RiskClass, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

SEARCH_CODEBASE_SPEC = ToolSpec(
    name="search_codebase",
    description="Searches files across the workspace using regex pattern matching with ripgrep or Python fallback.",
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
    """Pure-Python regex search fallback with identical semantics to ripgrep."""
    results: list[tuple[str, int, str]] = []

    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        # Skip ignored directories
        if any(part in IGNORE_DIRS for part in file_path.parts):
            continue
        # Apply glob pattern if present
        if glob_pattern:
            if glob_pattern.startswith("!"):
                if fnmatch.fnmatch(file_path.name, glob_pattern[1:]):
                    continue
            else:
                if not fnmatch.fnmatch(file_path.name, glob_pattern):
                    continue

        try:
            rel_path = str(file_path.relative_to(workspace))
        except ValueError:
            rel_path = str(file_path)

        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f, 1):
                    if query_pattern.search(line):
                        results.append((rel_path, idx, line.rstrip("\r\n")))
                        if len(results) >= limit:
                            return results
        except (OSError, PermissionError):
            continue

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
        search_root = (ctx.workspace / subpath).resolve()
    else:
        search_root = ctx.workspace

    if not search_root.exists() or not search_root.is_dir():
        return ToolResult.error(f"Error: search path does not exist or is not a directory: {subpath}")

    glob_filter = args.get("glob_filter")
    max_results = min(500, max(1, int(args.get("max_results", 50))))
    offset = max(0, int(args.get("offset", 0)))
    total_needed = offset + max_results
    fetch_limit = min(1000, total_needed + 1)

    matches: list[tuple[str, int, str]] | None = None
    rg_bin = shutil.which("rg")

    # Attempt ripgrep via ctx.executor if executor and exec capability are available
    if rg_bin and Capability.EXEC in ctx.capabilities and hasattr(ctx, "executor"):
        try:
            cmd = [
                rg_bin,
                "--line-number",
                "--no-heading",
                "--color", "never",
                "--glob", "!.git/*",
                "--glob", "!node_modules/*",
                "--glob", "!.venv/*",
                "--glob", "!dist/*",
                "--glob", "!build/*",
                "--glob", "!__pycache__/*",
                "--glob", "!.harness/*",
            ]
            if not case_sensitive:
                cmd.append("-i")
            if glob_filter:
                cmd.extend(["-g", glob_filter])
            cmd.extend(["-m", str(fetch_limit), query, str(search_root)])

            req = ExecRequest(command=cmd, cwd=search_root, timeout_s=30.0)
            proc = await ctx.executor.start(req)
            res = await proc.wait()
            # ripgrep returns 0 if matches found, 1 if no matches found
            if res.exit_code in (0, 1):
                matches = []
                for line in res.stdout.splitlines():
                    parts = line.split(":", 2)
                    if len(parts) == 3:
                        fpath, line_no, content = parts
                        try:
                            rel_path = str(Path(fpath).relative_to(ctx.workspace))
                        except ValueError:
                            rel_path = fpath
                        try:
                            l_num = int(line_no)
                        except ValueError:
                            continue
                        matches.append((rel_path, l_num, content.rstrip("\r\n")))
        except Exception as err:
            logger.debug(f"Ripgrep execution failed, falling back to Python search: {err}")
            matches = None

    # Fallback to pure-Python regex search if rg was not run or failed
    if matches is None:
        matches = _search_python(
            workspace=ctx.workspace,
            root=search_root,
            query_pattern=query_pattern,
            glob_pattern=glob_filter,
            limit=fetch_limit,
        )

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

"""
Filesystem tools: view_file, write_to_file, edit_file (§4, PLG-03).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from harness.sdk.cards import Card
from harness.sdk.context import ToolContext
from harness.sdk.types import RiskClass, TextBlock, ToolResult, ToolSpec

from .diffs import apply_search_replace

VIEW_FILE_SPEC = ToolSpec(
    name="view_file",
    description="Reads and displays the contents of a file in the workspace with optional line ranges.",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "format": "workspace-path",
                "description": "Path to the file to read relative to workspace",
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional 1-indexed starting line number",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional 1-indexed ending line number",
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.READ,
    capabilities=frozenset(["fs:read"]),
)

WRITE_FILE_SPEC = ToolSpec(
    name="write_to_file",
    description="Creates a new file or completely overwrites an existing file in the workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "format": "workspace-path",
                "description": "Path to the file to write relative to workspace",
            },
            "content": {
                "type": "string",
                "description": "Text content to write into the file",
            },
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.WRITE,
    capabilities=frozenset(["fs:write"]),
)

EDIT_FILE_SPEC = ToolSpec(
    name="edit_file",
    description="Applies surgical SEARCH/REPLACE diff blocks to an existing file in the workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "format": "workspace-path",
                "description": "Path to the file to edit relative to workspace",
            },
            "diff": {
                "type": "string",
                "description": "SEARCH/REPLACE diff blocks (<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE)",
            },
        },
        "required": ["file_path", "diff"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.WRITE,
    capabilities=frozenset(["fs:write"]),
)


def _resolve_target(file_path: str, ctx: ToolContext) -> Path:
    p = Path(file_path)
    if not p.is_absolute():
        p = ctx.workspace / p
    return p.resolve()


async def handle_view_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Views file contents with optional line range."""
    target = _resolve_target(args["file_path"], ctx)
    if not target.exists():
        return ToolResult.error(f"File not found: {args['file_path']}")
    if not target.is_file():
        return ToolResult.error(f"Path is not a regular file: {args['file_path']}")

    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as err:
        return ToolResult.error(f"Failed to read file: {err}")

    start_line = args.get("start_line", 1)
    end_line = args.get("end_line", len(lines))

    start_idx = max(0, start_line - 1)
    end_idx = min(len(lines), end_line)

    selected_lines = lines[start_idx:end_idx]
    numbered = [f"{start_idx + idx + 1:5d} | {line}" for idx, line in enumerate(selected_lines)]
    output_text = "".join(numbered) if selected_lines else "[Empty file or range]"

    # Read ledger (WS-03 read-before-edit): the hash is of the *whole file*, not the visible range,
    # so a partial read still proves the model saw this exact content. Recorded on the result, so it
    # lands in the transcript and survives a restart — no private store to drift from.
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return ToolResult(
        content=[TextBlock(text=output_text)],
        metadata={
            "path": str(target),
            "read_sha256": digest,
            "lines_total": len(lines),
            "lines_shown": [start_idx + 1, end_idx],
        },
    )


async def handle_write_to_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Writes text content directly to a file."""
    target = _resolve_target(args["file_path"], ctx)
    content = args["content"]

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(
            content=[TextBlock(text=f"Successfully wrote {len(content)} characters to {args['file_path']}")],
            # The write itself counts as the freshest read of the file: the model knows exactly what
            # is now on disk, so the next edit of the same path must not be blocked as "unread".
            metadata={
                "changed_files": [str(target)],
                "path": str(target),
                "read_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
        )
    except Exception as err:
        return ToolResult.error(f"Failed to write file: {err}")


async def handle_edit_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Applies surgical SEARCH/REPLACE diff blocks to a file."""
    target = _resolve_target(args["file_path"], ctx)
    diff_text = args["diff"]

    ok, msg, added, removed = apply_search_replace(target, diff_text)
    if not ok:
        return ToolResult.error(msg)

    diff_card = Card.diff(
        path=str(args["file_path"]),
        unified=diff_text,
        added=added,
        removed=removed,
    )

    return ToolResult(
        content=[TextBlock(text=msg)],
        cards=[diff_card],
        # After our own edit the model has an accurate view of the file, so the ledger records the
        # new hash instead of leaving the pre-edit read "stale" (WS-03 read-before-edit).
        metadata={
            "changed_files": [str(target)],
            "path": str(target),
            "read_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        },
    )

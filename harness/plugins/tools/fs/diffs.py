"""
Surgical search/replace diff applier for fs tools (§4, PLG-03).
"""
from __future__ import annotations

import re
from pathlib import Path

DIFF_BLOCK_PATTERN = re.compile(
    r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE", re.DOTALL
)


def apply_search_replace(
    file_path: Path, diff_text: str
) -> tuple[bool, str, int, int]:
    """
    Applies SEARCH/REPLACE blocks to a target file.
    Returns: (success: bool, message: str, added_lines: int, removed_lines: int)
    """
    if not file_path.exists():
        return False, f"File not found: {file_path}", 0, 0

    content = file_path.read_text(encoding="utf-8")
    matches = list(DIFF_BLOCK_PATTERN.finditer(diff_text))

    if not matches:
        return False, "No valid <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE blocks found.", 0, 0

    new_content = content
    total_added = 0
    total_removed = 0
    blocks_applied = 0

    for match in matches:
        search_block = match.group(1)
        replace_block = match.group(2)

        if search_block not in new_content:
            return (
                False,
                f"Search block not found in {file_path.name}:\n```\n{search_block}\n```",
                0,
                0,
            )

        new_content = new_content.replace(search_block, replace_block, 1)
        blocks_applied += 1
        total_removed += len(search_block.splitlines())
        total_added += len(replace_block.splitlines())

    file_path.write_text(new_content, encoding="utf-8")
    return (
        True,
        f"Successfully applied {blocks_applied} search/replace block(s) to {file_path.name}",
        total_added,
        total_removed,
    )

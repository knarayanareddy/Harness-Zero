"""
Search/Replace Block Diff Parser and Applier (Aider Pattern).
Applies deterministic surgical edits without whole-file hallucinations.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Tuple


class DiffApplier:
    PATTERN = re.compile(
        r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE", re.DOTALL
    )

    @classmethod
    def apply_diff(cls, file_path: Path, diff_block: str) -> Tuple[bool, str]:
        """
        Applies SEARCH/REPLACE blocks to file_path.
        Returns: (success: bool, message: str)
        """
        file_path = Path(file_path)
        if not file_path.exists():
            return False, f"File not found: {file_path}"

        content = file_path.read_text(encoding="utf-8")
        matches = list(cls.PATTERN.finditer(diff_block))
        if not matches:
            return False, "No valid <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE blocks found."

        new_content = content
        replacements_count = 0

        for match in matches:
            search_text = match.group(1)
            replace_text = match.group(2)

            if search_text not in new_content:
                # Try normalized whitespace
                return (
                    False,
                    f"Search block not found in {file_path}:\n```\n{search_text}\n```",
                )

            new_content = new_content.replace(search_text, replace_text, 1)
            replacements_count += 1

        file_path.write_text(new_content, encoding="utf-8")
        return True, f"Successfully applied {replacements_count} search/replace blocks to {file_path.name}"

    @classmethod
    def replace_line_range(
        cls,
        file_path: Path,
        start_line: int,
        end_line: int,
        target_content: str,
        replacement_content: str,
    ) -> Tuple[bool, str]:
        file_path = Path(file_path)
        if not file_path.exists():
            return False, f"File not found: {file_path}"

        lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if start_line < 1 or end_line > len(lines) or start_line > end_line:
            return (
                False,
                f"Invalid line range [{start_line}, {end_line}] for file with {len(lines)} lines",
            )

        chunk = "".join(lines[start_line - 1 : end_line])
        if target_content not in chunk:
            return False, "Target content does not match existing content in specified line range."

        new_chunk = chunk.replace(target_content, replacement_content, 1)
        new_lines = (
            lines[: start_line - 1]
            + [new_chunk]
            + lines[end_line:]
        )
        file_path.write_text("".join(new_lines), encoding="utf-8")
        return True, f"Successfully updated lines {start_line}-{end_line} in {file_path.name}"

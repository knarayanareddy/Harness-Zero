"""
Embedded Ripgrep Code Search Engine (ChatGPT Desktop Pattern).
High-speed regex codebase search matching ChatGPT's bundled rg binary.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional


@dataclass
class RipgrepMatch:
    file_path: str
    line_number: int
    line_content: str


class RipgrepSearcher:
    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root)
        self.rg_bin = shutil.which("rg")

    async def search(
        self,
        query: str,
        glob_filter: Optional[str] = None,
        case_sensitive: bool = False,
        max_results: int = 50,
    ) -> List[RipgrepMatch]:
        """Runs ripgrep if available, or Python fallback."""
        if self.rg_bin and Path(self.rg_bin).exists():
            return await self._search_rg(query, glob_filter, case_sensitive, max_results)
        return self._search_fallback(query, glob_filter, case_sensitive, max_results)

    async def _search_rg(
        self,
        query: str,
        glob_filter: Optional[str],
        case_sensitive: bool,
        max_results: int,
    ) -> List[RipgrepMatch]:
        cmd = [
            self.rg_bin,
            "--line-number",
            "--no-heading",
            "--color", "never",
            "--glob", "!.git/*",
            "--glob", "!node_modules/*",
            "--glob", "!.venv/*",
            "--glob", "!dist/*",
            "--glob", "!Library/*",
            "--glob", "!build/*",
        ]
        if not case_sensitive:
            cmd.append("-i")
        if glob_filter:
            cmd.extend(["-g", glob_filter])
        cmd.extend(["-m", str(max_results), query, str(self.workspace_root)])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        matches = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3:
                fpath, line_no, content = parts
                try:
                    rel_path = str(Path(fpath).relative_to(self.workspace_root))
                except ValueError:
                    rel_path = fpath
                matches.append(RipgrepMatch(rel_path, int(line_no), content.strip()))
                if len(matches) >= max_results:
                    break
        return matches

    def _search_fallback(
        self,
        query: str,
        glob_filter: Optional[str],
        case_sensitive: bool,
        max_results: int,
    ) -> List[RipgrepMatch]:
        import re

        pattern = re.compile(
            re.escape(query) if not case_sensitive else query,
            0 if case_sensitive else re.IGNORECASE,
        )
        matches = []
        ignore_dirs = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}

        for p in self.workspace_root.rglob("*"):
            if not p.is_file() or any(part in ignore_dirs for part in p.parts):
                continue
            if glob_filter and not p.match(glob_filter):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, start=1):
                        if pattern.search(line):
                            rel_path = str(p.relative_to(self.workspace_root))
                            matches.append(RipgrepMatch(rel_path, i, line.strip()))
                            if len(matches) >= max_results:
                                return matches
            except Exception:
                continue
        return matches

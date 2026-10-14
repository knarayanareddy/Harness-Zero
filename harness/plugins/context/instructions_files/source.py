"""
Project Instructions Files Context Source (§5.4, PLG-07).
Reads AGENTS.md, CLAUDE.md, and .harness/INSTRUCTIONS.md in the workspace root.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from harness.sdk.types import ContextBlock, Stability

logger = logging.getLogger(__name__)


class InstructionsFilesContextSource:
    """Emits project instruction files found in workspace root."""

    name: str = "instructions_files"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        workspace = getattr(ctx, "workspace", None)
        if not workspace:
            return []

        ws_path = Path(workspace)
        candidates = [
            ("AGENTS.md", ws_path / "AGENTS.md"),
            ("CLAUDE.md", ws_path / "CLAUDE.md"),
            (".harness/INSTRUCTIONS.md", ws_path / ".harness" / "INSTRUCTIONS.md"),
            (".github/copilot-instructions.md", ws_path / ".github" / "copilot-instructions.md"),
        ]

        blocks: list[ContextBlock] = []
        for name, path in candidates:
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace").strip()
                    if content:
                        clean_id = f"instructions_{name.replace('/', '_').replace('.', '_')}"
                        blocks.append(
                            ContextBlock(
                                id=clean_id,
                                text=f"# Project Instructions ({name}):\n{content}",
                                stability=Stability.STATIC,
                                priority=30,
                                source=self.name,
                                token_estimate=len(content) // 4,
                            )
                        )
                except Exception as err:
                    logger.debug(f"Failed to read instructions file {path}: {err}")

        return blocks

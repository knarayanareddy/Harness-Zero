"""
Skills Catalog Context Source (PLG-15).
Provides progressive disclosure of available skills to the prompt.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from harness.sdk.skill_resources import iter_skill_contents
from harness.sdk.types import ContextBlock, Stability

logger = logging.getLogger(__name__)


def parse_skill_meta(skill_file: Path, content: str) -> dict[str, str] | None:
    """Parse already-confined content; the display path is never reopened."""
    skill_dir = skill_file.parent
    try:
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1]) or {}
                name = str(frontmatter.get("name") or skill_dir.name)
                desc = str(frontmatter.get("description") or "").strip()
                return {"name": name, "description": desc, "path": str(skill_file)}

        # Fallback without frontmatter
        lines = content.strip().splitlines()
        desc = ""
        for line in lines:
            if line.startswith("#"):
                continue
            if line.strip():
                desc = line.strip()
                break
        return {"name": skill_dir.name, "description": desc, "path": str(skill_file)}
    except Exception as e:
        logger.debug("Failed parsing skill at %s: %s", skill_dir, e)
        return None


def discover_skills(workspace: Path | None = None) -> list[dict[str, str]]:
    """Discovers skills from builtin and workspace locations."""
    skills_map: dict[str, dict[str, str]] = {}

    for path, content in iter_skill_contents(workspace):
        meta = parse_skill_meta(path, content)
        if meta and meta["name"] not in skills_map:
            skills_map[meta["name"]] = meta

    return sorted(skills_map.values(), key=lambda s: s["name"])


class SkillsCatalogContextSource:
    """
    ContextSource listing available progressive skills in prompt context.
    """

    name: str = "skills_catalog"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        workspace = getattr(ctx, "workspace", None)
        skills = discover_skills(workspace)
        if not skills:
            return []

        lines = [
            "# Available Skills (Progressive Disclosure)",
            "The following specialized skill playbooks are available. Use the `view_skill` tool to read the complete instructions for any skill before executing it:",
            "",
        ]
        for s in skills:
            lines.append(f"- **{s['name']}**: {s['description']}")

        return [
            ContextBlock(
                id="skills_catalog",
                text="\n".join(lines),
                stability=Stability.STATIC,
            )
        ]

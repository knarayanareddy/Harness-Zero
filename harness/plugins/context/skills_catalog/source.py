"""
Skills Catalog Context Source (PLG-15).
Provides progressive disclosure of available skills to the prompt.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from harness.sdk.types import ContextBlock, Stability

logger = logging.getLogger(__name__)


def parse_skill_meta(skill_dir: Path) -> dict[str, str] | None:
    """Reads metadata from SKILL.md in a skill directory."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")
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

    search_dirs: list[Path] = []

    # 1. Built-in skills (locate relative to repo / package)
    builtin_skills = Path(__file__).resolve().parents[4] / "builtin" / "skills"
    if builtin_skills.exists():
        search_dirs.append(builtin_skills)

    # 2. Workspace skills
    if workspace:
        harness_skills = workspace / ".harness" / "skills"
        if harness_skills.exists():
            search_dirs.append(harness_skills)
        ws_skills = workspace / "skills"
        if ws_skills.exists():
            search_dirs.append(ws_skills)

    for base in search_dirs:
        for entry in sorted(base.iterdir()):
            if entry.is_dir():
                meta = parse_skill_meta(entry)
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

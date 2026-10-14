"""
Skills Tool: view_skill implementation (PLG-15).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from harness.sdk.cards import Card
from harness.sdk.context import ToolContext
from harness.sdk.types import RiskClass, TextBlock, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

VIEW_SKILL_SPEC = ToolSpec(
    name="view_skill",
    description="Loads and displays the complete playbook instructions and methodology for a specialized skill.",
    input_schema={
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "The identifier or name of the skill to view (e.g. 'code-architect', 'deep-research').",
            },
        },
        "required": ["skill_name"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.READ,
)


def find_skill_file(skill_name: str, workspace: Path | None) -> Path | None:
    """Finds SKILL.md for a given skill name in builtin or workspace directories."""
    search_dirs: list[Path] = []

    # 1. Built-in skills
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

    norm_name = skill_name.lower().replace("_", "-")
    for base in search_dirs:
        candidate = base / skill_name / "SKILL.md"
        if candidate.exists():
            return candidate
        candidate_norm = base / norm_name / "SKILL.md"
        if candidate_norm.exists():
            return candidate_norm
        for entry in base.iterdir():
            if entry.is_dir() and entry.name.lower().replace("_", "-") == norm_name:
                f = entry / "SKILL.md"
                if f.exists():
                    return f

    return None


class ViewSkillHandler:
    """Handles view_skill tool execution."""

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        skill_name = str(args.get("skill_name") or args.get("name") or "").strip()
        if not skill_name:
            return ToolResult(content=[TextBlock(text="Error: skill_name parameter is required")], is_error=True)

        workspace = getattr(ctx, "workspace", None)
        skill_file = find_skill_file(skill_name, workspace)
        if not skill_file:
            return ToolResult(content=[TextBlock(text=f"Error: skill '{skill_name}' was not found")], is_error=True)

        try:
            content = skill_file.read_text(encoding="utf-8")
            card = Card.markdown(content, title=f"Skill Playbook: {skill_name}")
            return ToolResult(
                content=[TextBlock(text=content)],
                cards=[card],
            )
        except Exception as e:
            logger.error("Failed reading skill file %s: %s", skill_file, e)
            return ToolResult(content=[TextBlock(text=f"Error reading skill '{skill_name}': {e}")], is_error=True)

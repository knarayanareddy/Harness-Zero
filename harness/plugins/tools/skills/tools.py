"""
Skills Tool: view_skill implementation (PLG-15).
"""
from __future__ import annotations

import logging
from typing import Any

from harness.sdk.cards import Card
from harness.sdk.context import ToolContext
from harness.sdk.skill_resources import read_skill
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


class ViewSkillHandler:
    """Handles view_skill tool execution."""

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        skill_name = str(args.get("skill_name") or args.get("name") or "").strip()
        if not skill_name:
            return ToolResult(content=[TextBlock(text="Error: skill_name parameter is required")], is_error=True)

        workspace = getattr(ctx, "workspace", None)
        content = read_skill(skill_name, workspace)
        if content is None:
            return ToolResult(content=[TextBlock(text=f"Error: skill '{skill_name}' was not found")], is_error=True)

        try:
            card = Card.markdown(content, title=f"Skill Playbook: {skill_name}")
            return ToolResult(
                content=[TextBlock(text=content)],
                cards=[card],
            )
        except Exception as e:
            logger.error("Failed displaying skill %s: %s", skill_name, e)
            return ToolResult(content=[TextBlock(text=f"Error reading skill '{skill_name}': {e}")], is_error=True)

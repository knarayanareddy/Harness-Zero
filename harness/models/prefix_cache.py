"""
Deterministic Prefix-Invariant Prompt Assembler.
Enforces the 5-tier layout to guarantee 90%+ prompt caching hit rates
across Anthropic, Gemini, OpenAI, and local models.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class PromptTiers:
    static_system_identity: str
    tool_schemas: list[dict[str, Any]]
    skills_catalog: str
    conversation_history: list[dict[str, Any]]
    current_user_prompt: str
    additional_metadata: str | None = None


class PromptCacheBuilder:
    @staticmethod
    def build_cached_prompt(tiers: PromptTiers) -> list[dict[str, Any]]:
        """
        Builds messages list strictly adhering to:
        Tier 1: Static System Identity (0-2k tokens)
        Tier 2: Sorted deterministic Tool JSON Schemas
        Tier 3: Progressive Skill Catalog
        Tier 4: Append-Only Conversation History
        Tier 5: Dynamic Turn Tail (user input + timestamp only here)
        """
        messages: list[dict[str, Any]] = []

        # Tier 1 + 2 + 3: Deterministic invariant system block
        # Sort tool schemas alphabetically by name to ensure stable prefix
        sorted_tools = sorted(
            tiers.tool_schemas, key=lambda x: x.get("name", x.get("function", {}).get("name", ""))
        )
        tools_block = json.dumps(sorted_tools, indent=2, sort_keys=True)

        system_sections = [
            tiers.static_system_identity.strip(),
            f"<available_tools>\n{tools_block}\n</available_tools>",
            tiers.skills_catalog.strip(),
        ]
        system_content = "\n\n".join(system_sections)

        messages.append({
            "role": "system",
            "content": system_content,
        })

        # Tier 4: Append-Only History (Must NOT modify previous turns in place)
        for turn in tiers.conversation_history:
            messages.append(turn)

        # Tier 5: Dynamic Tail (Timestamps and real-time metadata placed only here)
        now_str = datetime.now(UTC).isoformat()
        tail_parts = [
            f"<USER_REQUEST>\n{tiers.current_user_prompt}\n</USER_REQUEST>",
            f"<CONTEXT_TIMESTAMP>\nCurrent local time: {now_str}\n</CONTEXT_TIMESTAMP>",
        ]
        if tiers.additional_metadata:
            tail_parts.append(
                f"<ADDITIONAL_CONTEXT>\n{tiers.additional_metadata}\n</ADDITIONAL_CONTEXT>"
            )

        messages.append({
            "role": "user",
            "content": "\n\n".join(tail_parts),
        })

        return messages

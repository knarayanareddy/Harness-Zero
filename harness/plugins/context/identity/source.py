"""
Core Identity Context Source (§5.4, PLG-07).
Emits STATIC Tier-1 system prompt block establishing role and behavioral baseline.
"""
from __future__ import annotations

from typing import Any

from harness.sdk.types import ContextBlock, Stability


class IdentityContextSource:
    """Emits immutable STATIC identity prompt for the agent."""

    name: str = "identity"

    async def blocks(self, ctx: Any) -> list[ContextBlock]:
        text = (
            "You are Harness-Zero, an advanced agentic software engineering system. "
            "You operate with absolute precision, rigorous test-driven verification, and "
            "minimal disruption. Always inspect files thoroughly before editing, write clean "
            "production-grade code, and verify all changes with automated tests."
        )
        return [
            ContextBlock(
                id="identity",
                text=text,
                stability=Stability.STATIC,
                priority=10,
                source=self.name,
                token_estimate=len(text) // 4,
            )
        ]

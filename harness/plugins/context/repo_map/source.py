"""
Repo Map Context Source (§PLG-20).
Injects dense AST codebase map into SESSION stability tier within token limits.
"""
from __future__ import annotations

from harness.sdk.context import TurnContext
from harness.sdk.types import ContextBlock, Stability

from .map import RepoMapGenerator, calculate_workspace_fingerprint


class RepoMapContextSource:
    """Context source providing whole-codebase symbol overview."""

    name: str = "repo_map"

    def __init__(self) -> None:
        self.generator = RepoMapGenerator()
        self._cached_fingerprint: str | None = None
        self._cached_map: str | None = None

    async def blocks(self, ctx: TurnContext) -> list[ContextBlock]:
        max_tokens = 1024
        force = False

        if hasattr(ctx, "settings") and hasattr(ctx.settings, "get"):
            val = ctx.settings.get("context.repo_map.max_tokens")
            if val is not None:
                max_tokens = int(val)
            force_val = ctx.settings.get("context.repo_map.force")
            if force_val is not None:
                force = bool(force_val)

        self.generator.max_tokens = max_tokens or 1024

        ws = ctx.workspace
        # Cache check using fingerprint
        fingerprint = calculate_workspace_fingerprint(ws)
        if fingerprint == self._cached_fingerprint and self._cached_map is not None:
            map_text = self._cached_map
        else:
            map_text = self.generator.generate(ws, force=force)
            self._cached_fingerprint = fingerprint
            self._cached_map = map_text

        # Heuristic token estimate: len(text) // 4
        token_estimate = max(1, len(map_text) // 4)

        return [
            ContextBlock(
                id="repo_map",
                text=map_text,
                stability=Stability.SESSION,
                priority=80,
                source=self.name,
                token_estimate=token_estimate,
            )
        ]

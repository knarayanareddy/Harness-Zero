"""
Mock Provider for hermetic testing and sovereign offline execution.
Absorbs MDL-01. Returns scripted responses or deterministic [mock] text with zero tool calls.
"""
from __future__ import annotations

from typing import Any

from harness.core.types import ModelResponse


class MockProvider:
    """
    Hermetic Mock Provider.
    Does not execute keyword simulations or simulated tools.
    """
    def __init__(self, responses: list[ModelResponse] | None = None):
        self._responses = list(responses or [])

    def set_responses(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)

    def generate(self, messages: list[dict[str, Any]]) -> ModelResponse:
        if self._responses:
            return self._responses.pop(0)

        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = str(m.get("content", ""))
                break

        return ModelResponse(
            content=f"[mock] {last_user}",
            thinking="Deterministic hermetic mock response (no simulated tool calls).",
            tool_calls=[],
        )

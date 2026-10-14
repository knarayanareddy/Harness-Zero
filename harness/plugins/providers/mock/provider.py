"""
Mock Provider implementation (PLG-13).
Absorbs MDL-01 and Decision D1.
Deterministic offline execution: echoes [mock] text with zero tool calls unless a script is supplied.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from harness.sdk.testing import ScriptedProvider
from harness.sdk.types import (
    Done,
    ModelEvent,
    ModelInfo,
    ModelRequest,
    ProviderCaps,
    Role,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolCallEnd,
    Usage,
    UsageEvent,
)

logger = logging.getLogger(__name__)

DEFAULT_MOCK_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="mock-v1",
        display_name="Hermetic Mock Model",
        context_tokens=131072,
        input_usd_per_mtok=0.0,
        output_usd_per_mtok=0.0,
    ),
]


class MockProvider:
    """
    Hermetic Mock Provider with deterministic [mock] echo or script replay.
    """

    caps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=True,
        cache_hints=False,
        thinking=True,
        json_output=True,
        max_context_tokens=131072,
    )

    def __init__(
        self,
        name: str = "mock",
        script_file: Path | str | None = None,
        scripted_provider: ScriptedProvider | None = None,
    ) -> None:
        self.name = name
        self.script_file = Path(script_file).resolve() if script_file else None
        self._scripted = scripted_provider or ScriptedProvider(models=DEFAULT_MOCK_MODELS)
        self._has_script = False

        if self.script_file and self.script_file.exists():
            self._load_script(self.script_file)

    def _load_script(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            turns: list[list[ModelEvent]] = []

            for turn in data:
                events: list[ModelEvent] = []
                if isinstance(turn, dict):
                    if "thinking" in turn and turn["thinking"]:
                        events.append(ThinkingDelta(text=turn["thinking"]))
                    if "text" in turn and turn["text"]:
                        events.append(TextDelta(text=turn["text"]))
                    for i, tc in enumerate(turn.get("tool_calls", [])):
                        events.append(
                            ToolCallEnd(
                                id=tc.get("id", f"mock_call_{i+1}"),
                                name=tc.get("name", ""),
                                arguments=tc.get("arguments", {}),
                            )
                        )
                    if "usage" in turn:
                        u = turn["usage"]
                        events.append(
                            UsageEvent(
                                usage=Usage(
                                    input_tokens=u.get("input_tokens", 10),
                                    output_tokens=u.get("output_tokens", 10),
                                )
                            )
                        )
                    events.append(Done(stop_reason=turn.get("stop_reason", "stop")))
                turns.append(events)

            if turns:
                self._scripted.responses = list(turns)
                self._has_script = True
        except Exception as e:
            logger.error("Failed to load mock script from %s: %s", path, e)

    def set_scripted_responses(self, responses: list[list[ModelEvent] | Exception]) -> None:
        self._scripted.responses = list(responses)
        self._has_script = True

    async def aclose(self) -> None:
        await self._scripted.aclose()

    async def list_models(self) -> list[ModelInfo]:
        return DEFAULT_MOCK_MODELS

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        # If pre-scripted responses exist, replay them
        if self._has_script and self._scripted.responses:
            async for evt in self._scripted.generate(req):
                yield evt
            return

        # Default deterministic behavior: echo [mock] <user_text> with zero tool calls
        user_text = ""
        for m in reversed(req.messages):
            if m.role == Role.USER:
                user_text = "\n".join(b.text for b in m.content if isinstance(b, TextBlock))
                if user_text:
                    break

        if not user_text:
            user_text = "ping"

        yield TextDelta(text=f"[mock] {user_text}")
        yield UsageEvent(
            usage=Usage(
                input_tokens=len(user_text),
                output_tokens=len(user_text) + 7,
                cost_usd=0.0,
            )
        )
        yield Done(stop_reason="stop")

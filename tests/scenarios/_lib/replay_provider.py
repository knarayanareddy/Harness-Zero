"""Replay / recording providers implementing ``harness.sdk.contracts.Provider``.

``ReplayProvider`` differs from ``harness.sdk.testing.ScriptedProvider`` in two
ways that matter for golden tests: it loads from disk, and **exhaustion is an
error** (``ScriptedProvider`` silently yields ``Done(end_turn)`` when it runs out
of responses, which hides runaway loops).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from harness.sdk.types import (
    Done,
    ModelEvent,
    ModelInfo,
    ModelRequest,
    ProviderCaps,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from .codec import encode_event, load_streams

PROVIDER_NAME = "replay"
MODEL = ModelInfo(
    id="replay-v1",
    display_name="Replay (recorded)",
    context_tokens=128000,
    input_usd_per_mtok=1.0,
    output_usd_per_mtok=2.0,
)


class ReplayExhausted(RuntimeError):
    """The kernel asked for one more model response than the recording has."""


def request_summary(req: ModelRequest) -> dict[str, Any]:
    """A compact, deterministic description of what the model was shown."""
    msgs: list[dict[str, Any]] = []
    for m in req.messages:
        blocks: list[dict[str, Any]] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                blocks.append({"text": b.text})
            elif isinstance(b, ToolUseBlock):
                blocks.append({"tool_use": b.name, "input": b.input})
            elif isinstance(b, ToolResultBlock):
                inner = "".join(c.text for c in b.content if isinstance(c, TextBlock))
                blocks.append({"tool_result": inner, "is_error": b.is_error})
            else:
                blocks.append({"block": type(b).__name__})
        msgs.append({"role": getattr(m.role, "value", str(m.role)), "content": blocks})
    # Providers send the blocks separately (Anthropic) or join them with a blank line (openai_compat, ollama, gemini).
    system_text = "\n\n".join(b.text for b in req.system if b.text)
    return {
        "model": req.model,
        "system_chars": len(system_text),
        "system_head": system_text[:400],
        # Section headings make the prompt's composition visible in the golden without freezing its full text.
        "system_sections": [ln.strip() for ln in system_text.splitlines() if ln.startswith("#")],
        "tools": sorted(t.name for t in req.tools),
        "messages": msgs,
        # Full text for `model_saw` / `model_never_saw`; stripped from goldens (see golden_view).
        "system_text": system_text,
    }


def golden_view(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The request summaries as stored in requests.json (without the volatile full system text)."""
    return [{k: v for k, v in s.items() if k != "system_text"} for s in summaries]


class ReplayProvider:
    name = PROVIDER_NAME
    caps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=False,
        cache_hints=False,
        thinking=True,
        json_output=True,
    )

    def __init__(self, streams: list[list[ModelEvent]], *, model: ModelInfo = MODEL, strict: bool = True) -> None:
        self._streams = streams
        self._model = model
        self._strict = strict
        self._index = 0
        self.requests: list[ModelRequest] = []

    @classmethod
    def from_file(cls, path: Path, **kw: Any) -> ReplayProvider:
        return cls(load_streams(path), **kw)

    @property
    def remaining(self) -> int:
        return len(self._streams) - self._index

    def is_configured(self) -> bool:
        return True

    async def is_reachable(self) -> bool | None:
        return None

    async def list_models(self) -> list[ModelInfo]:
        return [self._model]

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(req)
        if self._index >= len(self._streams):
            if self._strict:
                raise ReplayExhausted(
                    f"model request #{self._index + 1} has no recording (recorded {len(self._streams)} requests)"
                )
            yield Done(stop_reason="end_turn")
            return
        stream = self._streams[self._index]
        self._index += 1
        for ev in stream:
            yield ev

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        return None


class RecordingProvider:
    """Tees a real provider into ``provider.jsonl`` + ``provider.requests.json``.

    Only used with ``HARNESS_REPLAY=record`` and a configured real provider.
    Refuses to write if any value of an environment variable that looks like a
    key (``*_API_KEY``, ``*_TOKEN``) appears in the recorded text.
    """

    def __init__(self, inner: Any, scenario_dir: Path) -> None:
        self.inner = inner
        self.name = PROVIDER_NAME
        self.caps = inner.caps
        self.scenario_dir = scenario_dir
        self.streams: list[list[ModelEvent]] = []
        self.requests: list[ModelRequest] = []

    def is_configured(self) -> bool:
        return bool(self.inner.is_configured())

    async def is_reachable(self) -> bool | None:
        return await self.inner.is_reachable()

    async def list_models(self) -> list[ModelInfo]:
        return await self.inner.list_models()

    async def generate(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(req)
        stream: list[ModelEvent] = []
        async for ev in self.inner.generate(req):
            stream.append(ev)
            yield ev
        self.streams.append(stream)

    async def aclose(self) -> None:
        await self.inner.aclose()

    def flush(self) -> None:
        import os

        text = "\n".join(encode_event(ev) for s in self.streams for ev in s)
        for k, v in os.environ.items():
            if (k.endswith("_API_KEY") or k.endswith("_TOKEN")) and v and v in text:
                raise RuntimeError(f"refusing to record: value of ${k} appears in the stream")
        from .codec import write_streams

        write_streams(self.scenario_dir / "provider.jsonl", self.streams)
        (self.scenario_dir / "provider.requests.json").write_text(
            json.dumps([request_summary(r) for r in self.requests], indent=2, sort_keys=True) + "\n"
        )

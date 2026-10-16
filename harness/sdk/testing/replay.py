"""
Recorded-provider replay and recording (§11).

`ScriptedProvider` in ``scripted.py`` is the convenient one: hand it a list of event lists, it answers
turns in order and ends politely when it runs out. This module is the same idea for *golden* tests, where
politeness is the problem — a run that asks for a sixth response when five were recorded is a behaviour
change, not a detail:

* :class:`ReplayProvider` answers from a ``provider.jsonl`` file and is strict by default.
* :class:`RecordingProvider` wraps a real provider, streams it through unchanged, and writes that file —
  plus a ``provider.requests.json`` sidecar naming the provider and model the stream came from, so the
  replay can identify itself the same way and be reproduced by someone with different credentials.

The file format is one JSON object per ``ModelEvent`` tagged with ``"t": <class name>``, a blank line
between requests. It is deliberately trivial: readable in a diff, appendable by hand, and the same whether
the events came from a real API or from `make_recordings.py`-style generators.
"""
from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from harness.sdk.testing.scripted import ReplayExhausted
from harness.sdk.types import (
    Done,
    ModelEvent,
    ModelInfo,
    ModelRequest,
    ProviderCaps,
    ProviderNotice,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    UsageEvent,
)

REPLAY_PROVIDER_NAME = "replay"
REPLAY_MODEL = ModelInfo(
    id="replay-v1",
    display_name="Replay (recorded)",
    context_tokens=128000,
    input_usd_per_mtok=1.0,
    output_usd_per_mtok=2.0,
)


# --------------------------------------------------------------------------- codec

_TYPES: dict[str, type] = {
    c.__name__: c
    for c in (
        TextDelta,
        ThinkingDelta,
        ToolCallStart,
        ToolCallDelta,
        ToolCallEnd,
        UsageEvent,
        ProviderNotice,
        Done,
    )
}


def encode_event(ev: ModelEvent) -> str:
    d = dataclasses.asdict(ev)
    d["t"] = type(ev).__name__
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


def decode_event(line: str) -> ModelEvent:
    d = json.loads(line)
    t = d.pop("t")
    if t == "UsageEvent":
        return UsageEvent(usage=Usage(**d["usage"]))
    try:
        cls = _TYPES[t]
    except KeyError as e:  # pragma: no cover - defensive
        raise ValueError(f"unknown ModelEvent tag {t!r}") from e
    return cls(**d)


def load_streams(path: Path) -> list[list[ModelEvent]]:
    """Reads a recording into one event list per model request."""
    streams: list[list[ModelEvent]] = []
    current: list[ModelEvent] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if current:
                streams.append(current)
                current = []
            continue
        current.append(decode_event(line))
    if current:
        streams.append(current)
    return streams


def dump_streams(streams: list[list[ModelEvent]]) -> str:
    chunks = ["\n".join(encode_event(ev) for ev in stream) for stream in streams]
    return "\n\n".join(chunks) + "\n"


def write_streams(path: Path, streams: list[list[ModelEvent]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_streams(streams), encoding="utf-8")


def request_summary(req: ModelRequest) -> dict[str, Any]:
    """A compact, deterministic description of what the model was shown.

    Written into the sidecar by :meth:`RecordingProvider.flush` and compared as a golden by test suites, so
    the volatile full system text is excluded here (suites re-add it only for substring assertions) and
    section headings are pulled out to show *what composed* the prompt without freezing its whole body.
    """
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
        "system_sections": [ln.strip() for ln in system_text.splitlines() if ln.startswith("#")],
        "tools": sorted(t.name for t in req.tools),
        "messages": msgs,
        # Full text for substring assertions; a suite strips it before writing a golden.
        "system_text": system_text,
    }


class ReplayProvider:
    """Answers from recorded streams, one per model request. Strict by default."""

    name = REPLAY_PROVIDER_NAME
    caps = ProviderCaps(
        native_tools=True,
        streaming=True,
        vision=False,
        cache_hints=False,
        thinking=True,
        json_output=True,
    )

    def __init__(
        self, streams: list[list[ModelEvent]], *, model: ModelInfo = REPLAY_MODEL, strict: bool = True
    ) -> None:
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

    Two design points, both learned the hard way by the scenario suite:

    * **It keeps the key the caller's configuration already selects** (``replay`` by default), and whoever
      installs it should take the wrapped provider *out* of the registry for the duration of the run.
      Frontends send the provider of the model in their picker, so while the wrapped provider is still
      registered a request can resolve past the tee: the run records nothing and still looks normal, because
      the real provider answers with plausible text. Keying the tee as the wrapped provider instead collides
      with its own plugin, which a registry rightly refuses.
    * **It refuses to write a stream that contains a credential** — the value of any environment variable
      ending in ``_API_KEY`` or ``_TOKEN`` — because a recording is meant to be committed.
    """

    def __init__(self, inner: Any, directory: Path, *, provider_name: str = REPLAY_PROVIDER_NAME) -> None:
        self.inner = inner
        self.name = provider_name
        self.caps = inner.caps
        self.directory = directory
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
        """Writes both files. Idempotent, and safe to call before a second boot replays the recording."""
        text = "\n".join(encode_event(ev) for s in self.streams for ev in s)
        for key, value in os.environ.items():
            if (key.endswith("_API_KEY") or key.endswith("_TOKEN")) and value and value in text:
                raise RuntimeError(f"refusing to record: value of ${key} appears in the stream")
        write_streams(self.directory / "provider.jsonl", self.streams)
        # The sidecar says *what* was recorded. Model and provider ids appear in `turn.started`,
        # `context.assembled` and a UI's model picker, so a replay has to name the same ones or nobody else
        # can reproduce the run.
        (self.directory / "provider.requests.json").write_text(
            json.dumps(
                {
                    "provider": self.inner.name,
                    "model": (self.requests[0].model if self.requests else ""),
                    "requests": [request_summary(r) for r in self.requests],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


__all__ = [
    "REPLAY_MODEL",
    "ReplayExhausted",
    "REPLAY_PROVIDER_NAME",
    "RecordingProvider",
    "ReplayProvider",
    "decode_event",
    "dump_streams",
    "encode_event",
    "load_streams",
    "request_summary",
    "write_streams",
]

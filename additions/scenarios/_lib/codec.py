"""JSONL codec for recorded provider streams (``provider.jsonl``).

One JSON object per ``ModelEvent`` line, tagged with ``"t": <class name>``.
A blank line separates consecutive model requests.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from harness.sdk.types import (
    Done,
    ModelEvent,
    ProviderNotice,
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
    UsageEvent,
)

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
    """Reads ``provider.jsonl`` into one event list per model request."""
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

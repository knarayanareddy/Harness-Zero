"""Deterministic normalisation of transcripts and events before golden comparison.

Rules (applied recursively to dicts/lists/strings):
  * keys that are pure noise are dropped (timestamps, durations, costs, byte sizes)
  * ids are replaced by stable ordinals in first-seen order (``evt#1``, ``call#1`` …)
  * absolute workspace / tmp paths become ``<ws>`` / ``<tmp>``
  * the active session id becomes ``<session>``
The same normaliser is used for both lanes so a value cannot be "stable" in one
golden and "noise" in the other.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DROP_KEYS = frozenset(
    {
        "ts",
        "timestamp",
        "created_at",
        "updated_at",
        "duration_ms",
        "duration_s",
        "elapsed_ms",
        "latency_ms",
        "cost_usd",
        "turn_usd",
        "session_usd",
        "total_usd",
        "usd",
        "estimated_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "tokens",
        "bytes",
        "size",
        "pid",
        "workspace",
        "timeout_s",
    }
)

_ID_KEYS = {
    "id": "evt",
    "call_id": "call",
    "tool_use_id": "call",
    "approval_id": "appr",
    "request_id": "appr",
    "turn_id": "turn",
    "task_id": "task",
    "form_id": "form",
    "card_id": "card",
    "checkpoint": "ckpt",
    "sha": "sha",
}

_HEX_ID = re.compile(r"\b(evt|turn|call|appr|req|task|sess|session|child|ckpt|card|form)_[0-9a-f]{6,}\b")
_UUIDISH = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b")
_FLOAT_S = re.compile(r"\b\d+\.\d+s\b")


class Normalizer:
    def __init__(self, *, workspace: Path | None = None, session_id: str | None = None, extra_paths: dict[str, str] | None = None):
        self._ordinals: dict[str, dict[str, str]] = {}
        self._paths: list[tuple[str, str]] = []
        if workspace is not None:
            ws = str(Path(workspace).resolve())
            self._paths.append((ws, "<ws>"))
            self._paths.append((str(workspace), "<ws>"))
        for raw, token in (extra_paths or {}).items():
            self._paths.append((raw, token))
        self._paths.append(("/tmp/", "<tmp>/"))
        self._paths.append(("/private/tmp/", "<tmp>/"))
        self.session_id = session_id

    def ordinal(self, kind: str, value: str) -> str:
        table = self._ordinals.setdefault(kind, {})
        if value not in table:
            table[value] = f"{kind}#{len(table) + 1}"
        return table[value]

    def text(self, s: str) -> str:
        if self.session_id:
            s = s.replace(self.session_id, "<session>")
        for raw, token in self._paths:
            if raw and raw in s:
                s = s.replace(raw, token)
        s = _UUIDISH.sub(lambda m: self.ordinal("uuid", m.group(0)), s)
        s = _HEX_ID.sub(lambda m: self.ordinal(m.group(1), m.group(0)), s)
        s = _ISO_TS.sub("<ts>", s)
        s = _FLOAT_S.sub("<n>s", s)
        return s

    def value(self, v: Any, key: str | None = None) -> Any:
        if isinstance(v, dict):
            out: dict[str, Any] = {}
            for k in sorted(v):
                if k in DROP_KEYS:
                    continue
                out[k] = self.value(v[k], k)
            return out
        if isinstance(v, list):
            return [self.value(x, key) for x in v]
        if isinstance(v, str):
            if key == "session_id":
                return "<session>" if (self.session_id is None or v == self.session_id) else self.ordinal("session", v)
            if key in _ID_KEYS and v:
                return self.ordinal(_ID_KEYS[key], v)
            return self.text(v)
        if isinstance(v, float):
            return round(v, 3)
        return v

    def events(self, events: list[dict[str, Any]], *, drop_types: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
        out = []
        for e in events:
            if e.get("type") in drop_types:
                continue
            out.append(self.value(e))
        return out

    def transcript(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.value(r) for r in records]


# Live/high-frequency events whose *count* depends on chunking, not behaviour.
LIVE_NOISE = frozenset({"model.text.delta", "model.thinking.delta", "tool.output.delta", "cost.updated", "context.assembled"})


def collapse_deltas(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merges consecutive ``model.text.delta`` events into one with the joined text
    so goldens do not depend on chunk boundaries."""
    out: list[dict[str, Any]] = []
    for e in events:
        if e.get("type") in ("model.text.delta", "model.thinking.delta") and out and out[-1].get("type") == e.get("type"):
            prev = out[-1]
            prev_text = prev.get("data", {}).get("text", "") or prev.get("data", {}).get("delta", "")
            text = e.get("data", {}).get("text", "") or e.get("data", {}).get("delta", "")
            merged = dict(prev)
            merged["data"] = dict(prev.get("data", {}))
            if "text" in merged["data"] or "text" in e.get("data", {}):
                merged["data"]["text"] = prev_text + text
            else:
                merged["data"]["delta"] = prev_text + text
            out[-1] = merged
            continue
        out.append(e)
    return out

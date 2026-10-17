"""The scenario suite's view of the SDK's replay machinery.

The providers, the codec and the request summaries live in `harness.sdk.testing.replay` (WS-01), so anyone
writing a plugin can record and replay the same way these tests do. What stays here is only what the
*scenario* format knows: the name every scenario pins `kernel.provider` to, the recorded-model lookup, and
the golden projection.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from harness.sdk.testing.replay import (
    REPLAY_MODEL,
    REPLAY_PROVIDER_NAME,
    RecordingProvider,
    ReplayProvider,
    request_summary,
)
from harness.sdk.types import ModelInfo

# Every scenario selects this provider, in both modes: `ReplayProvider` answers from the fixture, and in
# record mode `RecordingProvider` is registered under the same key with the real provider stepped aside.
PROVIDER_NAME = REPLAY_PROVIDER_NAME
MODEL = REPLAY_MODEL


def recording_meta(sc: Any) -> dict[str, Any]:
    """What the record path wrote beside the stream: ``{"provider": …, "model": …, "requests": […]}``.

    Empty dict for a scenario that was never recorded (every synthetic fixture here), which is what keeps
    the committed goldens untouched: they replay as ``replay`` / ``replay-v1`` as always.
    """
    path = Path(sc.dir) / "provider.requests.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}  # a malformed sidecar is not worth failing a run over; the recording still replays
    if isinstance(data, list):  # an older record run wrote the request list directly
        return {"requests": data}
    return data if isinstance(data, dict) else {}


def scenario_model(sc: Any) -> ModelInfo:
    """The model a scenario's own recording names, or the scripted fixture's default.

    The recorded model id is *inside* the goldens (`turn.started`, `context.assembled`, the UI's model chip),
    so a scenario recorded from ``claude-x`` has to replay as ``claude-x`` — pinning one constant for every
    fixture would make a recorded scenario unrepeatable by anyone who was not the one who recorded it. The
    sidecar's ``provider`` field is for whoever reviews the fixture (it says where the stream came from).
    """
    model_id = str(recording_meta(sc).get("model") or "")
    if not model_id or model_id == MODEL.id:
        return MODEL
    return replace(MODEL, id=model_id)


_PLATFORM_SPECIFIC_TOOLS = frozenset({"take_screenshot"})


def golden_view(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project requests without volatile prompts or host-only tool registrations.

    Screenshot availability is macOS-specific and is covered by its plugin tests. The flow goldens describe
    the portable tool surface, so the same request fixture remains valid on Linux and macOS.
    """
    out = []
    for summary in summaries:
        row = {key: value for key, value in summary.items() if key != "system_text"}
        tools = row.get("tools")
        if isinstance(tools, list):
            row["tools"] = [name for name in tools if name not in _PLATFORM_SPECIFIC_TOOLS]
        out.append(row)
    return out


__all__ = [
    "MODEL",
    "PROVIDER_NAME",
    "RecordingProvider",
    "ReplayProvider",
    "golden_view",
    "recording_meta",
    "request_summary",
    "scenario_model",
]

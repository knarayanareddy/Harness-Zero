"""`harness.sdk.testing.replay` — the recording format, and what makes it strict (WS-01).

These are the SDK-level guarantees the scenario suite rests on. The behaviour that matters is the asymmetry
between convenience and evidence: a script that runs out answers politely *unless* you ask for
`strict_exhaustion`, because a golden test needs "one request too many" to be a failure, not a shrug.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from harness.sdk.testing import (
    REPLAY_MODEL,
    REPLAY_PROVIDER_NAME,
    RecordingProvider,
    ReplayExhausted,
    ReplayProvider,
    ScriptedProvider,
    decode_event,
    load_streams,
    request_summary,
    write_streams,
)
from harness.sdk.types import (
    ContextBlock,
    Done,
    Message,
    ModelInfo,
    ModelParams,
    ModelRequest,
    ProviderCaps,
    Role,
    Stability,
    TextBlock,
    TextDelta,
    ToolCallEnd,
    Usage,
    UsageEvent,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "scenarios" / "plain_answer_kernel" / "provider.jsonl"


def _req(model: str = "unit-v1", text: str = "hi") -> ModelRequest:
    return ModelRequest(
        model=model,
        system=[ContextBlock(id="persona", text="# Persona\nbe brief\n\n# Tools\ntools here", stability=Stability.STATIC)],
        messages=[Message(role=Role.USER, content=[TextBlock(text=text)])],
        tools=[],
        params=ModelParams(),
    )


class _Inner:
    """A stand-in 'real' provider: fixed streams, no network."""

    name = "unitmock"
    caps = ProviderCaps(
        native_tools=False, streaming=True, vision=True, cache_hints=False, thinking=False, json_output=True
    )

    def __init__(self, streams: list[list]) -> None:
        self.streams = streams
        self.requests: list[ModelRequest] = []
        self.closed = False

    def is_configured(self) -> bool:
        return True

    async def is_reachable(self) -> bool | None:
        return None

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="unit-v1", display_name="Unit", context_tokens=1000, input_usd_per_mtok=0.0, output_usd_per_mtok=0.0)]

    async def generate(self, req: ModelRequest):
        self.requests.append(req)
        for ev in self.streams[len(self.requests) - 1]:
            yield ev

    async def aclose(self) -> None:
        self.closed = True


# ------------------------------------------------------------------ the polite/strict split


async def test_a_script_that_runs_out_ends_politely_by_default():
    provider = ScriptedProvider([[TextDelta(text="a"), Done(stop_reason="end_turn")]])
    first = [ev async for ev in provider.generate(_req())]
    assert [type(ev) for ev in first] == [TextDelta, Done]
    second = [ev async for ev in provider.generate(_req())]
    assert [type(ev) for ev in second] == [Done], "an over-run must still terminate the turn"
    assert second[0].stop_reason == "end_turn"


async def test_exhaustion_is_an_error_when_the_script_is_strict():
    provider = ScriptedProvider([], strict_exhaustion=True)
    assert provider.remaining == 0
    with pytest.raises(ReplayExhausted, match="no scripted response"):
        [ev async for ev in provider.generate(_req())]


async def test_a_replay_refuses_a_request_it_has_no_recording_for(tmp_path: Path):
    path = tmp_path / "provider.jsonl"
    write_streams(path, [[TextDelta(text="hello"), Done(stop_reason="end_turn")]])
    provider = ReplayProvider.from_file(path)
    assert provider.remaining == 1
    assert [ev.text for ev in [e async for e in provider.generate(_req())] if isinstance(ev, TextDelta)] == ["hello"]
    assert provider.remaining == 0
    with pytest.raises(ReplayExhausted, match="recorded 1 requests"):
        [ev async for ev in provider.generate(_req())]
    # …and politely, when the caller says the flow may ask again: the recording still answers the requests
    # it has, and only the request past the end becomes an empty turn instead of an error.
    lenient = ReplayProvider.from_file(path, strict=False)
    assert len([ev async for ev in lenient.generate(_req())]) == 2
    assert [type(ev) for ev in [e async for e in lenient.generate(_req())]] == [Done]


async def test_a_replay_identifies_itself_as_the_provider_and_model_it_was_recorded_from():
    provider = ReplayProvider.from_file(FIXTURE, model=replace(REPLAY_MODEL, id="claude-sonnet-4-5"))
    assert provider.name == REPLAY_PROVIDER_NAME, "the fixture key is what a scenario pins `kernel.provider` to"
    assert provider.is_configured() is True
    assert [m.id for m in await provider.list_models()] == ["claude-sonnet-4-5"]
    assert await provider.is_reachable() is None, "reachable-unknown, so `models.list` groups it as ready"


# ------------------------------------------------------------------ the codec


def test_the_committed_recordings_are_a_fixed_point_of_the_codec(tmp_path: Path):
    """Decoding and re-encoding a real `provider.jsonl` returns its exact bytes.

    That is what makes a recording reviewable in a diff: the file in git is the file the recorder wrote, in
    the format the loader reads, with no normalising step in between.
    """
    streams = load_streams(FIXTURE)
    assert streams and all(streams), "the fixture must decode into one non-empty stream per request"
    out = tmp_path / "provider.jsonl"
    write_streams(out, streams)
    assert out.read_text(encoding="utf-8") == FIXTURE.read_text(encoding="utf-8")
    assert load_streams(out) == streams


def test_a_blank_line_or_comment_separates_requests(tmp_path: Path):
    path = tmp_path / "provider.jsonl"
    path.write_text('{"t": "TextDelta", "text": "one"}\n\n# a note\n{"t": "TextDelta", "text": "two"}\n', encoding="utf-8")
    streams = load_streams(path)
    assert [[ev.text for ev in stream] for stream in streams] == [["one"], ["two"]]


def test_an_unknown_tag_is_reported_rather_than_guessed():
    with pytest.raises(ValueError, match="unknown ModelEvent tag"):
        decode_event(json.dumps({"t": "TeleportEvent"}))
    # UsageEvent nests its payload, so it needs the one special case in the decoder.
    ev = decode_event(json.dumps({"t": "UsageEvent", "usage": {"input_tokens": 3, "output_tokens": 4}}))
    assert isinstance(ev, UsageEvent) and ev.usage == Usage(input_tokens=3, output_tokens=4)


# ------------------------------------------------------------------ the recorder


async def test_the_recorder_writes_the_stream_it_passed_through(tmp_path: Path):
    inner = _Inner([[TextDelta(text="hello"), ToolCallEnd(id="call_1", name="write", arguments={"p": 1})]])
    recorder = RecordingProvider(inner, tmp_path)
    assert recorder.name == REPLAY_PROVIDER_NAME, "the tee answers to the key the caller already selects"
    assert recorder.caps is inner.caps, "capabilities come from the real provider, or a run tests the wrong caps"
    streamed = [ev async for ev in recorder.generate(_req())]
    assert [type(ev) for ev in streamed] == [TextDelta, ToolCallEnd]
    recorder.flush()
    assert load_streams(tmp_path / "provider.jsonl") == [[TextDelta(text="hello"), ToolCallEnd(id="call_1", name="write", arguments={"p": 1})]]
    meta = json.loads((tmp_path / "provider.requests.json").read_text(encoding="utf-8"))
    assert meta["provider"] == "unitmock", "who the stream came from, for whoever reviews the fixture"
    assert meta["model"] == "unit-v1", "the model id has to be replayable by someone else"
    assert [r["model"] for r in meta["requests"]] == ["unit-v1"]
    recorder.flush()  # idempotent: a runner that flushes twice must not append twice


async def test_the_recorder_refuses_to_write_a_credential(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UNIT_TEST_API_KEY", "sk-super-secret-value")
    recorder = RecordingProvider(_Inner([[TextDelta(text="the key is sk-super-secret-value")]]), tmp_path)
    [ev async for ev in recorder.generate(_req())]
    with pytest.raises(RuntimeError, match=r"\$UNIT_TEST_API_KEY"):
        recorder.flush()
    assert not (tmp_path / "provider.jsonl").exists(), "a fixture that would leak is not written at all"


def test_the_sidecar_describes_what_the_model_was_shown():
    summary = request_summary(_req(text="question"))
    assert summary["model"] == "unit-v1"
    assert summary["system_sections"] == ["# Persona", "# Tools"], "the prompt's composition, without freezing its body"
    assert summary["messages"] == [{"role": "user", "content": [{"text": "question"}]}]
    assert "question" in summary["system_text"] + json.dumps(summary["messages"])

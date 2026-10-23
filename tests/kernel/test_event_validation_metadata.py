"""G1: repeated stream validation reuses declarations, never payload verdicts."""
from __future__ import annotations

from dataclasses import dataclass

from harness.sdk import events


def test_stream_validation_resolves_declarations_once_but_checks_each_payload(monkeypatch):
    @dataclass
    class Payload:
        text: str
        count: int

    calls = []
    resolve = events.get_type_hints

    def counted(cls):
        calls.append(cls)
        return resolve(cls)

    monkeypatch.setattr(events, "get_type_hints", counted)
    monkeypatch.setitem(events.EVENT_PAYLOADS, "test.stream.cache", Payload)
    for index in range(200):
        assert events.validate_event({"type": "test.stream.cache", "data": {"text": str(index), "count": index}}) == []
    errors = events.validate_event({"type": "test.stream.cache", "data": {"text": "next", "count": "not an integer"}})
    assert any("expected int" in error for error in errors)
    missing = events.validate_event({"type": "test.stream.cache", "data": {"text": "last", "extra": 1}})
    assert any("missing required field 'count'" in error for error in missing)
    assert any("unexpected field 'extra'" in error for error in missing)
    assert calls == [Payload]

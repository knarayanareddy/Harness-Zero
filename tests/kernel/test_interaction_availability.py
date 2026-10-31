from __future__ import annotations

import asyncio
import time

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.interactions import InteractionBroker
from harness.kernel.transcript import TranscriptLog

QUESTION = {
    "questions": [
        {
            "id": "format",
            "question": "Which format?",
            "options": [{"id": "md", "label": "Markdown"}, {"id": "html", "label": "HTML"}],
        }
    ]
}


def test_subscriber_copies_declared_interaction_kinds_as_immutable_metadata() -> None:
    kinds = {"approval"}
    bus = EventBus()

    subscriber = bus.subscribe(interaction_kinds=kinds)

    kinds.add("question")
    assert subscriber.interaction_kinds == frozenset({"approval"})


def test_legacy_registration_preserves_generic_and_both_kind_availability() -> None:
    bus = EventBus()
    legacy = bus.subscribe(types={"approval.required"}, interactive=True)

    assert legacy.interaction_kinds is None
    assert bus.has_interactive_subscribers("session-a")
    assert bus.has_interactive_subscriber("session-a", interaction_kind="approval")
    assert bus.has_interactive_subscriber("session-a", interaction_kind="question")


def test_kind_availability_is_separate_from_frame_filter_and_session_scope() -> None:
    bus = EventBus()
    question_peer = bus.subscribe(
        types={"approval.required"},
        interactive=True,
        session_id="session-a",
        interaction_kinds={"question"},
    )

    assert bus.has_interactive_subscriber("session-a", interaction_kind="question")
    assert not bus.has_interactive_subscribers("session-a", interaction_kind="approval")
    assert not bus.has_interactive_subscribers("session-b", interaction_kind="question")

    bus.unsubscribe(question_peer)
    assert not bus.has_interactive_subscriber("session-a", interaction_kind="question")


def test_passive_and_empty_kind_subscribers_never_provide_interaction_availability() -> None:
    bus = EventBus()
    bus.subscribe(interactive=True, interaction_kinds=set())
    bus.subscribe(interactive=False, interaction_kinds={"approval", "question"})
    bus.subscribe(interactive=True, session_id="other", interaction_kinds={"approval"})

    assert not bus.has_interactive_subscriber("session-a", interaction_kind="approval")
    assert not bus.has_interactive_subscriber("session-a", interaction_kind="question")


@pytest.mark.parametrize("kinds", [{"permission"}, "approval", ["approval", 1]])
def test_registration_rejects_invalid_interaction_kind_metadata(kinds: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        EventBus().subscribe(interaction_kinds=kinds)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_approval_only_receiver_does_not_publish_or_record_a_question() -> None:
    session_id = "session-approval-only"
    bus = EventBus()
    bus.subscribe(
        types={"approval.required"},
        interactive=True,
        session_id=session_id,
        interaction_kinds={"approval"},
    )
    observer = bus.subscribe(interactive=False)
    log = TranscriptLog(None, session_id=session_id)
    broker = InteractionBroker(
        bus=bus,
        timeout_s=0.05,
        log_for=lambda requested_session: log if requested_session == session_id else None,
    )

    started = time.monotonic()
    answer = await broker.ask("question", QUESTION, session_id=session_id)

    assert time.monotonic() - started < 0.5
    assert answer is None
    assert broker.pending() == []
    assert log.records == []
    assert observer.empty()


@pytest.mark.asyncio
async def test_approval_only_receiver_can_publish_and_resolve_approval() -> None:
    session_id = "session-approval"
    bus = EventBus()
    bus.subscribe(interactive=True, session_id=session_id, interaction_kinds={"approval"})
    observer = bus.subscribe(interactive=False)
    log = TranscriptLog(None, session_id=session_id)
    broker = InteractionBroker(
        bus=bus,
        timeout_s=1.0,
        log_for=lambda requested_session: log if requested_session == session_id else None,
    )
    request = asyncio.create_task(
        broker.ask("approval", {"summary": "Run the command?"}, session_id=session_id)
    )

    published = await asyncio.wait_for(observer.get(), timeout=1.0)
    assert published.type == "approval.required"
    assert broker.pending("approval", session_id)
    assert await broker.resolve_approval_recorded(published.data["approval_id"], approved=True)
    assert await asyncio.wait_for(request, timeout=1.0) == (True, "")
    resolved = await asyncio.wait_for(observer.get(), timeout=1.0)

    assert resolved.type == "approval.resolved"
    assert [record["kind"] for record in log.records] == ["approval.requested", "approval.resolved"]


@pytest.mark.asyncio
async def test_legacy_question_queue_still_supports_both_broker_interactions() -> None:
    session_id = "session-legacy-peer"
    bus = EventBus()
    question_peer = bus.subscribe(
        types={"question.asked", "question.answered"},
        interactive=True,
        session_id=session_id,
    )
    passive_approval_observer = bus.subscribe(interactive=False)
    log = TranscriptLog(None, session_id=session_id)
    broker = InteractionBroker(
        bus=bus,
        timeout_s=1.0,
        log_for=lambda requested_session: log if requested_session == session_id else None,
    )

    approval = asyncio.create_task(
        broker.ask("approval", {"summary": "Run the command?"}, session_id=session_id)
    )
    approval_request = await asyncio.wait_for(passive_approval_observer.get(), timeout=1.0)
    assert approval_request.type == "approval.required"
    assert broker.pending("approval", session_id)
    assert await broker.resolve_approval_recorded(approval_request.data["approval_id"], approved=True)
    assert await asyncio.wait_for(approval, timeout=1.0) == (True, "")
    assert (await asyncio.wait_for(passive_approval_observer.get(), timeout=1.0)).type == "approval.resolved"

    question = asyncio.create_task(broker.ask("question", QUESTION, session_id=session_id))
    asked = await asyncio.wait_for(question_peer.get(), timeout=1.0)
    assert asked.type == "question.asked"
    assert broker.pending("question", session_id)
    assert await broker.resolve_question(asked.data["interaction_id"], {"format": "md"})
    assert await asyncio.wait_for(question, timeout=1.0) == {
        "format": {"option_id": "md", "custom_text": ""}
    }
    assert (await asyncio.wait_for(question_peer.get(), timeout=1.0)).type == "question.answered"
    assert [record["kind"] for record in log.records] == [
        "approval.requested",
        "approval.resolved",
        "question.asked",
        "question.answered",
    ]

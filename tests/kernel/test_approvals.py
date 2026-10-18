"""Regression tests for the legacy ApprovalBroker facade over InteractionBroker."""
from __future__ import annotations

import asyncio

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.transcript import TranscriptLog


@pytest.mark.asyncio
async def test_approval_events_and_records_keep_the_existing_contract() -> None:
    bus = EventBus()
    subscriber = bus.subscribe(interactive=True)
    log = TranscriptLog(None, session_id="session_approval")
    broker = ApprovalBroker(
        bus=bus,
        timeout_s=2.0,
        log_for=lambda session_id: log if session_id == "session_approval" else None,
    )
    task = asyncio.create_task(
        broker.ask(
            "tool_execution",
            "Run the command?",
            {"command": "make test"},
            call_id="call_approval",
            session_id="session_approval",
        )
    )
    try:
        requested = await asyncio.wait_for(subscriber.get(), timeout=1.0)
        assert requested.type == "approval.required"
        assert requested.data == {
            "approval_id": requested.data["approval_id"],
            "call_id": "call_approval",
            "kind": "tool_execution",
            "summary": "Run the command?",
            "details": {"command": "make test"},
            "timeout_s": 2.0,
            "options": ["approve", "deny"],
        }
        assert requested.record["kind"] == "approval.requested"

        resolved = await broker.resolve_recorded(
            requested.data["approval_id"],
            approved=True,
            by="tester",
            reason="looks safe",
        )
        assert resolved
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result == (True, "looks safe")
        answered = await asyncio.wait_for(subscriber.get(), timeout=1.0)
        assert answered.type == "approval.resolved"
        assert answered.record["kind"] == "approval.resolved"
        assert answered.data == {
            "approval_id": requested.data["approval_id"],
            "approved": True,
            "by": "tester",
            "reason": "looks safe",
        }
        assert [record["kind"] for record in log.records] == [
            "approval.requested",
            "approval.resolved",
        ]
        assert log.records[1]["metadata"]["approval_kind"] == "tool_execution"
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        bus.unsubscribe(subscriber)


@pytest.mark.asyncio
async def test_headless_approval_is_denied_and_auto_approval_is_unchanged() -> None:
    bus = EventBus()
    broker = ApprovalBroker(bus=bus)
    assert await broker.ask("tool_execution", "Run it?") == (False, "non-interactive session")
    assert await broker.ask("tool_execution", "Run it?", auto_approve=True) == (True, "auto_approved")
    assert not broker.pending()

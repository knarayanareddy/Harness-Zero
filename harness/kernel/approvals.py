"""
ApprovalBroker for managing interactive and non-interactive user approval gates.
Absorbs TOOL-02 and satisfies KRN-06 Step 5.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from harness.kernel.bus import EventBus
from harness.sdk.events import Event

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    id: str
    call_id: str | None
    kind: str
    summary: str
    details: dict[str, Any]
    created_at: float
    timeout_s: float
    future: asyncio.Future[tuple[bool, str]]
    resolved: bool = False
    approved: bool = False
    resolved_by: str = ""
    resolution_reason: str = ""


class ApprovalBroker:
    """
    Manages pending approval requests, timeout enforcement,
    non-interactive denial, and event emission.
    """

    def __init__(self, bus: EventBus | None = None, timeout_s: float = 300.0):
        self.bus = bus
        self.timeout_s = timeout_s
        self._pending: dict[str, ApprovalRequest] = {}

    async def ask(
        self,
        kind: str,
        summary: str,
        details: dict[str, Any] | None = None,
        call_id: str | None = None,
        timeout_s: float | None = None,
        auto_approve: bool = False,
        session_id: str = "",
    ) -> tuple[bool, str]:
        """
        Requests approval for a tool or action.
        Returns (approved: bool, reason: str).
        """
        # 1. Fast-path: auto_approve granted
        if auto_approve:
            return True, "auto_approved"

        # 2. Non-interactive session: if no interactive subscriber, deny immediately without waiting
        if self.bus is not None and not self.bus.has_interactive_subscribers():
            return False, "non-interactive session"

        req_id = f"appr_{uuid.uuid4().hex[:12]}"
        effective_timeout = timeout_s if timeout_s is not None else self.timeout_s
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[bool, str]] = loop.create_future()

        req = ApprovalRequest(
            id=req_id,
            call_id=call_id,
            kind=kind,
            summary=summary,
            details=details or {},
            created_at=time.time(),
            timeout_s=effective_timeout,
            future=future,
        )
        self._pending[req_id] = req

        # Emit approval.required event
        if self.bus:
            evt = Event(
                type="approval.required",
                session_id=session_id,
                data={
                    "approval_id": req_id,
                    "call_id": call_id,
                    "kind": kind,
                    "summary": summary,
                    "details": details or {},
                    "timeout_s": effective_timeout,
                    "options": ["approve", "deny"],
                },
            )
            self.bus.publish(evt)

        try:
            async with asyncio.timeout(effective_timeout):
                approved, reason = await future
                return approved, reason
        except TimeoutError:
            req.resolved = True
            req.approved = False
            req.resolved_by = "timeout"
            req.resolution_reason = "timed out"
            if self.bus:
                evt = Event(
                    type="approval.resolved",
                    session_id=session_id,
                    data={
                        "approval_id": req_id,
                        "approved": False,
                        "by": "timeout",
                        "reason": "timed out",
                    },
                )
                self.bus.publish(evt)
            return False, "timed out"
        finally:
            self._pending.pop(req_id, None)

    def resolve(
        self,
        approval_id: str,
        approved: bool,
        by: str = "user",
        reason: str = "",
        session_id: str = "",
    ) -> bool:
        """Resolves a pending approval request."""
        req = self._pending.get(approval_id)
        if not req or req.resolved or req.future.done():
            return False

        req.resolved = True
        req.approved = approved
        req.resolved_by = by
        req.resolution_reason = reason
        req.future.set_result((approved, reason))

        if self.bus:
            evt = Event(
                type="approval.resolved",
                session_id=session_id,
                data={
                    "approval_id": approval_id,
                    "approved": approved,
                    "by": by,
                    "reason": reason,
                },
            )
            self.bus.publish(evt)
        return True

    def pending(self) -> list[dict[str, Any]]:
        """Returns metadata for all currently pending approvals."""
        return [
            {
                "approval_id": r.id,
                "call_id": r.call_id,
                "kind": r.kind,
                "summary": r.summary,
                "details": r.details,
                "created_at": r.created_at,
                "timeout_s": r.timeout_s,
            }
            for r in self._pending.values()
            if not r.resolved
        ]

    # Alias for API compatibility
    list_pending = pending

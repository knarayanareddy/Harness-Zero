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
from harness.sdk.events import ApprovalRequired, ApprovalResolved, Event
from harness.sdk.types import TranscriptRecord

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
    #: Which session asked. The resolved event needs it (a client filters events by session, and without
    #: it the pending card in the right window never closes), and so does the record.
    session_id: str = ""
    resolved: bool = False
    approved: bool = False
    resolved_by: str = ""
    resolution_reason: str = ""


class ApprovalBroker:
    """
    Manages pending approval requests, timeout enforcement,
    non-interactive denial, and event emission.
    """

    def __init__(
        self,
        bus: EventBus | None = None,
        timeout_s: float = 300.0,
        log_for: Any = None,
    ):
        self.bus = bus
        self.timeout_s = timeout_s
        self._pending: dict[str, ApprovalRequest] = {}
        #: `session_id -> TranscriptLog | None`, wired by the bootstrap once the session manager exists.
        #: The broker is kernel-wide while logs are per session, so it resolves rather than holds one.
        self.log_for = log_for

    async def _record(
        self, session_id: str, kind: str, content: str, metadata: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Writes the approval fact to the session log before announcing it (WS-04 log-before-emit).

        An approval that exists only as a live event disappears on reload — the user then sees a tool call
        with no explanation of who allowed it. A log that cannot be written does *not* decide the approval:
        the decision the user already made stands, and the denial/allow itself is recorded by the gate.
        """
        if self.log_for is None or not session_id:
            return None
        try:
            log = self.log_for(session_id)
            if log is None:
                return None
            await log.append(TranscriptRecord(kind=kind, role="system", content=content, metadata=metadata))
            return log.records[-1]
        except Exception as e:  # pragma: no cover - defensive: an unrecordable approval must still resolve
            logger.warning(f"Could not record {kind} in the session log: {e}")
            return None

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
            session_id=session_id,
        )
        self._pending[req_id] = req

        rec = await self._record(
            session_id,
            "approval.requested",
            summary,
            {
                "approval_id": req_id,
                "call_id": call_id,
                "approval_kind": kind,
                "details": details or {},
                "timeout_s": effective_timeout,
            },
        )

        # Emit approval.required event
        if self.bus:
            evt = Event(
                record=rec,
                type="approval.required",
                session_id=session_id,
                data=ApprovalRequired(
                    approval_id=req_id,
                    call_id=call_id,
                    kind=kind,
                    summary=summary,
                    details=details or {},
                    timeout_s=effective_timeout,
                    options=["approve", "deny"],
                ),
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
            rec = await self._record(
                session_id,
                "approval.resolved",
                f"Approval for {req.kind} timed out after {effective_timeout:.0f}s.",
                {"approval_id": req_id, "call_id": call_id, "approved": False, "by": "timeout"},
            )
            if self.bus:
                evt = Event(
                    record=rec,
                    type="approval.resolved",
                    session_id=session_id,
                    data=ApprovalResolved(
                        approval_id=req_id,
                        approved=False,
                        by="timeout",
                        reason="timed out",
                    ),
                )
                self.bus.publish(evt)
            return False, "timed out"
        finally:
            self._pending.pop(req_id, None)

    async def resolve_recorded(
        self,
        approval_id: str,
        approved: bool,
        by: str = "user",
        reason: str = "",
        session_id: str = "",
    ) -> bool:
        """`resolve` for async callers: writes the record first, then settles the future.

        Kept separate from `resolve` because the latter is called from synchronous code (a CLI keypress,
        a test) that cannot await a store write; those callers get the event without a record pointer,
        which the client handles by reading the log.
        """
        req = self._pending.get(approval_id)
        sid = session_id or (req.session_id if req else "")
        summary = req.summary if req else approval_id
        what = req.kind if req else "action"
        rec = await self._record(
            sid,
            "approval.resolved",
            f"{'Approved' if approved else 'Denied'}: {summary}",
            {
                "approval_id": approval_id,
                "call_id": req.call_id if req else None,
                "approval_kind": what,
                "approved": approved,
                "by": by,
                "reason": reason,
            },
        )
        return self.resolve(
            approval_id, approved, by=by, reason=reason, session_id=sid, record=rec
        )

    def resolve(
        self,
        approval_id: str,
        approved: bool,
        by: str = "user",
        reason: str = "",
        session_id: str = "",
        record: dict[str, Any] | None = None,
    ) -> bool:
        """Resolves a pending approval request.

        `record` is the `approval.resolved` record the *caller* already wrote: resolving is synchronous
        (it settles a future inside a running turn) so it cannot await the log itself, and `commands.respond_approval`
        appends first and hands the stored dict over — which keeps the ordering guarantee (log, then event)
        without making this call await.
        """
        req = self._pending.get(approval_id)
        if not req or req.resolved or req.future.done():
            return False

        req.resolved = True
        req.approved = approved
        req.resolved_by = by
        req.resolution_reason = reason
        req.future.set_result((approved, reason))

        session_id = session_id or req.session_id
        if self.bus:
            evt = Event(
                record=record,
                type="approval.resolved",
                session_id=session_id,
                data=ApprovalResolved(
                    approval_id=approval_id,
                    approved=approved,
                    by=by,
                    reason=reason,
                ),
            )
            self.bus.publish(evt)
        return True

    def peek(self, approval_id: str) -> ApprovalRequest | None:
        """The pending request, for callers that need its context (session, call, summary)."""
        return self._pending.get(approval_id)

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

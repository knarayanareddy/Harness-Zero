"""Backward-compatible approval API backed by the generalized interaction broker."""
from __future__ import annotations

import asyncio
from typing import Any

from harness.kernel.bus import EventBus
from harness.kernel.interactions import InteractionBroker, InteractionRequest

# Kept as an import-compatible name for embedders that annotated the old request type.
ApprovalRequest = InteractionRequest


class ApprovalBroker:
    """Thin approval-specific facade preserving the pre-WS-08 public API and wire behavior."""

    def __init__(
        self,
        bus: EventBus | None = None,
        timeout_s: float = 300.0,
        log_for: Any = None,
        *,
        interactions: InteractionBroker | None = None,
    ) -> None:
        self.interactions = interactions or InteractionBroker(
            bus=bus,
            timeout_s=timeout_s,
            log_for=log_for,
        )

    @property
    def bus(self) -> EventBus | None:
        return self.interactions.bus

    @bus.setter
    def bus(self, value: EventBus | None) -> None:
        self.interactions.bus = value

    @property
    def timeout_s(self) -> float:
        return self.interactions.timeout_s

    @timeout_s.setter
    def timeout_s(self, value: float) -> None:
        self.interactions.timeout_s = value

    @property
    def log_for(self) -> Any:
        return self.interactions.log_for

    @log_for.setter
    def log_for(self, value: Any) -> None:
        self.interactions.log_for = value

    @property
    def _pending(self) -> dict[str, InteractionRequest]:
        """Compatibility view; new code should use ``interactions.pending()``."""
        return self.interactions._pending

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
        """Request approval and return ``(approved, reason)`` as before WS-08."""
        result = await self.interactions.ask(
            "approval",
            {
                "kind": kind,
                "summary": summary,
                "details": details or {},
                "call_id": call_id,
            },
            timeout_s=timeout_s,
            auto_approve=auto_approve,
            session_id=session_id,
        )
        return result

    async def resolve_recorded(
        self,
        approval_id: str,
        approved: bool,
        by: str = "user",
        reason: str = "",
        session_id: str = "",
    ) -> bool:
        """Persist approval resolution, then settle the request and publish its event."""
        return await self.interactions.resolve_approval_recorded(
            approval_id=approval_id,
            approved=approved,
            by=by,
            reason=reason,
            session_id=session_id,
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
        """Synchronous compatibility path used by existing callers and tests."""
        return self.interactions.resolve_approval(
            approval_id,
            approved,
            by=by,
            reason=reason,
            session_id=session_id,
            record=record,
        )

    def peek(self, approval_id: str) -> InteractionRequest | None:
        """Returns the pending approval request, or ``None`` for unknown/question ids."""
        request = self.interactions.peek(approval_id)
        if request is None or request.interaction_type != "approval":
            return None
        return request

    def pending(self) -> list[dict[str, Any]]:
        """Returns pending approvals in the established response shape."""
        return self.interactions.pending(kind="approval")

    list_pending = pending

    def _ensure_pending_future(self, approval_id: str) -> asyncio.Future[Any] | None:
        """A small compatibility helper for callers that inspected the old internal request."""
        request = self.peek(approval_id)
        return request.future if request is not None else None

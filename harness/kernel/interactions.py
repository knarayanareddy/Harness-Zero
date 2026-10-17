"""Shared request/response broker for approvals and structured user questions."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from harness.kernel.bus import EventBus
from harness.sdk.events import (
    ApprovalRequired,
    ApprovalResolved,
    Event,
    QuestionAnswered,
    QuestionAsked,
)
from harness.sdk.types import TranscriptRecord

logger = logging.getLogger(__name__)
InteractionKind = Literal["approval", "question"]


class InteractionError(ValueError):
    """A request cannot be created or an answer does not match its question."""


@dataclass(slots=True)
class InteractionRequest:
    """One in-flight interaction, independent of the UI that will resolve it."""

    id: str
    interaction_type: InteractionKind
    payload: dict[str, Any]
    created_at: float
    timeout_s: float
    future: asyncio.Future[Any]
    session_id: str = ""
    turn_id: str | None = None
    resolved: bool = False
    resolving: bool = False
    approved: bool = False
    resolved_by: str = ""
    resolution_reason: str = ""

    @property
    def interaction_id(self) -> str:
        return self.id

    @property
    def call_id(self) -> str | None:
        return self.payload.get("call_id")

    @property
    def kind(self) -> str:
        """Legacy approval kind (for example ``tool_execution``)."""
        return str(self.payload.get("kind", self.interaction_type))

    @property
    def summary(self) -> str:
        return str(self.payload.get("summary", ""))

    @property
    def details(self) -> dict[str, Any]:
        return dict(self.payload.get("details") or {})


class InteractionBroker:
    """Creates, persists, emits, and resolves blocking human interactions.

    Approval requests retain the historic ``approval.required`` / ``approval.resolved`` wire contract.
    Questions use durable ``question.asked`` / ``question.answered`` records and events.
    """

    def __init__(
        self,
        bus: EventBus | None = None,
        timeout_s: float = 300.0,
        log_for: Any = None,
    ) -> None:
        self.bus = bus
        self.timeout_s = timeout_s
        self.log_for = log_for
        self._pending: dict[str, InteractionRequest] = {}
        self._resolution_lock = asyncio.Lock()

    async def _record(
        self,
        session_id: str,
        kind: str,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Append a durable interaction fact before emitting its event (WS-04 log-before-emit)."""
        if self.log_for is None or not session_id:
            return None
        try:
            log = self.log_for(session_id)
            if log is None:
                return None
            await log.append(
                TranscriptRecord(kind=kind, role="system", content=content, metadata=metadata)
            )
            return log.records[-1]
        except Exception as err:  # pragma: no cover - a human decision must still settle on a log failure
            logger.warning("Could not record %s in the session log: %s", kind, err)
            return None

    def _has_interactive_client(self, session_id: str, kind: InteractionKind) -> bool:
        if self.bus is None:
            # Historically approvals with no EventBus could still be resolved by an embedding caller.
            return kind == "approval"
        return self.bus.has_interactive_subscriber(session_id)

    async def ask(
        self,
        kind: InteractionKind,
        payload: dict[str, Any],
        *,
        timeout_s: float | None = None,
        auto_approve: bool = False,
        session_id: str = "",
        turn_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> Any:
        """Publish a request and await its answer.

        Approval answers are ``(approved, reason)`` tuples. Question answers are normalized mappings, or
        ``None`` when no interactive client is connected, the question times out, or its turn is cancelled.
        """
        if kind not in ("approval", "question"):
            raise ValueError(f"unsupported interaction kind: {kind!r}")
        if kind == "approval" and auto_approve:
            return True, "auto_approved"

        if not self._has_interactive_client(session_id, kind):
            if kind == "approval":
                return False, "non-interactive session"
            return None

        request_payload = dict(payload)
        if kind == "question":
            request_payload["questions"] = self._validate_questions(request_payload.get("questions"))
            if any(
                req.interaction_type == "question"
                and not req.resolved
                and req.session_id == session_id
                and (req.turn_id == turn_id if turn_id is not None else req.turn_id is None)
                for req in self._pending.values()
            ):
                raise InteractionError("A question is already pending for this turn.")

        effective_timeout = float(self.timeout_s if timeout_s is None else timeout_s)
        request_id = f"{'appr' if kind == 'approval' else 'ques'}_{uuid.uuid4().hex[:12]}"
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        request = InteractionRequest(
            id=request_id,
            interaction_type=kind,
            payload=request_payload,
            created_at=time.time(),
            timeout_s=effective_timeout,
            future=future,
            session_id=session_id,
            turn_id=turn_id,
        )
        self._pending[request_id] = request

        record = await self._record_request(request)
        self._publish_asked(request, record)

        cancel_task: asyncio.Task[bool] | None = None
        try:
            waiters: set[asyncio.Future[Any] | asyncio.Task[bool]] = {future}
            if kind == "question" and cancel_event is not None:
                cancel_task = asyncio.create_task(cancel_event.wait())
                waiters.add(cancel_task)
            done, _pending = await asyncio.wait(
                waiters,
                timeout=effective_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if future in done:
                answer = future.result()
                if kind == "question":
                    return answer["answers"] if answer["status"] == "answered" else None
                return answer

            if kind == "question":
                cancelled = cancel_task is not None and cancel_task in done
                await self.resolve_question(
                    request_id,
                    {},
                    status="cancelled" if cancelled else "timeout",
                    by="user" if cancelled else "timeout",
                    reason="Turn cancelled by user" if cancelled else "Question timed out",
                )
                return None

            return await self._timeout_approval(request)
        except asyncio.CancelledError:
            if kind == "question" and not request.resolved:
                await asyncio.shield(
                    self.resolve_question(
                        request_id,
                        {},
                        status="cancelled",
                        by="user",
                        reason="Question handler cancelled",
                    )
                )
            raise
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
            if not future.done():
                future.cancel()
            self._pending.pop(request_id, None)

    async def _record_request(self, request: InteractionRequest) -> dict[str, Any] | None:
        if request.interaction_type == "approval":
            return await self._record(
                request.session_id,
                "approval.requested",
                request.summary,
                {
                    "approval_id": request.id,
                    "call_id": request.call_id,
                    "approval_kind": request.kind,
                    "details": request.details,
                    "timeout_s": request.timeout_s,
                },
            )

        payload = request.payload
        questions = payload["questions"]
        metadata = {
            "interaction_id": request.id,
            "call_id": request.call_id,
            "turn_id": request.turn_id,
            "questions": questions,
            "timeout_s": request.timeout_s,
        }
        summary = "\n".join(str(q["question"]) for q in questions)
        return await self._record(
            request.session_id,
            "question.asked",
            f"User input requested:\n{summary}",
            metadata,
        )

    def _publish_asked(
        self, request: InteractionRequest, record: dict[str, Any] | None
    ) -> None:
        if self.bus is None:
            return
        if request.interaction_type == "approval":
            payload = request.payload
            event = Event(
                record=record,
                type="approval.required",
                session_id=request.session_id,
                data=ApprovalRequired(
                    approval_id=request.id,
                    call_id=request.call_id,
                    kind=request.kind,
                    summary=request.summary,
                    details=request.details,
                    timeout_s=request.timeout_s,
                    options=["approve", "deny"],
                ),
            )
        else:
            payload = request.payload
            event = Event(
                record=record,
                type="question.asked",
                session_id=request.session_id,
                turn_id=request.turn_id,
                data=QuestionAsked(
                    interaction_id=request.id,
                    call_id=request.call_id,
                    turn_id=request.turn_id,
                    questions=payload["questions"],
                    timeout_s=request.timeout_s,
                ),
            )
        self.bus.publish(event)

    async def _timeout_approval(self, request: InteractionRequest) -> tuple[bool, str]:
        async with self._resolution_lock:
            if request.resolved:
                return request.future.result() if request.future.done() else (False, "timed out")
            request.resolving = True
            record = await self._record(
                request.session_id,
                "approval.resolved",
                f"Approval for {request.kind} timed out after {request.timeout_s:.0f}s.",
                {
                    "approval_id": request.id,
                    "call_id": request.call_id,
                    "approved": False,
                    "by": "timeout",
                },
            )
            request.resolved = True
            request.approved = False
            request.resolved_by = "timeout"
            request.resolution_reason = "timed out"
            if not request.future.done():
                request.future.set_result((False, "timed out"))
            request.resolving = False
            if self.bus is not None:
                self.bus.publish(
                    Event(
                        record=record,
                        type="approval.resolved",
                        session_id=request.session_id,
                        data=ApprovalResolved(
                            approval_id=request.id,
                            approved=False,
                            by="timeout",
                            reason="timed out",
                        ),
                    )
                )
            return False, "timed out"

    async def resolve_approval_recorded(
        self,
        approval_id: str,
        approved: bool,
        by: str = "user",
        reason: str = "",
        session_id: str = "",
    ) -> bool:
        """Durably records an approval answer before settling its waiter and publishing the event."""
        async with self._resolution_lock:
            request = self._pending.get(approval_id)
            sid = session_id or (request.session_id if request else "")
            summary = request.summary if request else approval_id
            approval_kind = request.kind if request else "action"
            record = await self._record(
                sid,
                "approval.resolved",
                f"{'Approved' if approved else 'Denied'}: {summary}",
                {
                    "approval_id": approval_id,
                    "call_id": request.call_id if request else None,
                    "approval_kind": approval_kind,
                    "approved": approved,
                    "by": by,
                    "reason": reason,
                },
            )
            return self.resolve_approval(
                approval_id,
                approved,
                by=by,
                reason=reason,
                session_id=sid,
                record=record,
            )

    def resolve_approval(
        self,
        approval_id: str,
        approved: bool,
        by: str = "user",
        reason: str = "",
        session_id: str = "",
        record: dict[str, Any] | None = None,
    ) -> bool:
        """Synchronous compatibility path for CLI keypresses and existing embedders."""
        request = self._pending.get(approval_id)
        if (
            request is None
            or request.interaction_type != "approval"
            or request.resolved
            or request.future.done()
        ):
            return False
        request.resolved = True
        request.approved = approved
        request.resolved_by = by
        request.resolution_reason = reason
        request.future.set_result((approved, reason))
        if self.bus is not None:
            self.bus.publish(
                Event(
                    record=record,
                    type="approval.resolved",
                    session_id=session_id or request.session_id,
                    data=ApprovalResolved(
                        approval_id=approval_id,
                        approved=approved,
                        by=by,
                        reason=reason,
                    ),
                )
            )
        return True

    async def resolve_question(
        self,
        interaction_id: str,
        answers: dict[str, Any],
        *,
        status: Literal["answered", "cancelled", "timeout"] = "answered",
        by: str = "user",
        reason: str = "",
    ) -> bool:
        """Validate and durably settle a pending question interaction."""
        async with self._resolution_lock:
            request = self._pending.get(interaction_id)
            if (
                request is None
                or request.interaction_type != "question"
                or request.resolved
                or request.resolving
                or request.future.done()
            ):
                return False
            if status not in ("answered", "cancelled", "timeout"):
                raise InteractionError(f"invalid question resolution status: {status!r}")
            if status == "answered":
                normalized = self._normalize_answers(request.payload["questions"], answers)
            else:
                normalized = {}

            request.resolving = True
            metadata = {
                "interaction_id": interaction_id,
                "call_id": request.call_id,
                "turn_id": request.turn_id,
                "answers": normalized,
                "status": status,
                "by": by,
                "reason": reason,
            }
            content = {
                "answered": "User answered the questions.",
                "cancelled": "The pending question was cancelled.",
                "timeout": "The pending question timed out.",
            }[status]
            record = await self._record(
                request.session_id,
                "question.answered",
                content,
                metadata,
            )
            request.resolved = True
            request.resolving = False
            request.resolved_by = by
            request.resolution_reason = reason
            result = {"answers": normalized, "status": status}
            if not request.future.done():
                request.future.set_result(result)
            if self.bus is not None:
                self.bus.publish(
                    Event(
                        record=record,
                        type="question.answered",
                        session_id=request.session_id,
                        turn_id=request.turn_id,
                        data=QuestionAnswered(
                            interaction_id=interaction_id,
                            call_id=request.call_id,
                            turn_id=request.turn_id,
                            answers=normalized,
                            status=status,
                            by=by,
                            reason=reason,
                        ),
                    )
                )
            return True

    async def resolve(
        self,
        interaction_id: str,
        answer: Any,
        *,
        by: str = "user",
        reason: str = "",
    ) -> bool:
        """Generic answer entry point: dispatches to approval or question resolution by request type."""
        request = self._pending.get(interaction_id)
        if request is None:
            return False
        if request.interaction_type == "approval":
            if isinstance(answer, dict):
                approved = bool(answer.get("approved", False))
                reason = str(answer.get("reason", reason))
            else:
                approved = bool(answer)
            return await self.resolve_approval_recorded(
                interaction_id, approved, by=by, reason=reason
            )
        if isinstance(answer, dict) and "answers" in answer:
            return await self.resolve_question(
                interaction_id,
                answer.get("answers") or {},
                status=answer.get("status", "answered"),
                by=by,
                reason=str(answer.get("reason", reason)),
            )
        return await self.resolve_question(interaction_id, answer, by=by, reason=reason)

    def is_pending_question(self, interaction_id: str) -> bool:
        request = self._pending.get(interaction_id)
        return bool(
            request
            and request.interaction_type == "question"
            and not request.resolved
            and not request.resolving
        )

    def peek(self, interaction_id: str) -> InteractionRequest | None:
        return self._pending.get(interaction_id)

    def pending(
        self,
        kind: InteractionKind | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Returns a JSON-friendly summary of currently pending interactions."""
        out: list[dict[str, Any]] = []
        for request in self._pending.values():
            if request.resolved or request.resolving:
                continue
            if kind is not None and request.interaction_type != kind:
                continue
            if session_id is not None and request.session_id != session_id:
                continue
            if request.interaction_type == "approval":
                out.append(
                    {
                        "approval_id": request.id,
                        "call_id": request.call_id,
                        "kind": request.kind,
                        "summary": request.summary,
                        "details": request.details,
                        "created_at": request.created_at,
                        "timeout_s": request.timeout_s,
                    }
                )
            else:
                out.append(
                    {
                        "interaction_id": request.id,
                        "call_id": request.call_id,
                        "turn_id": request.turn_id,
                        "questions": request.payload["questions"],
                        "created_at": request.created_at,
                        "timeout_s": request.timeout_s,
                    }
                )
        return out

    def list_pending(self) -> list[dict[str, Any]]:
        return self.pending()

    @staticmethod
    def _validate_questions(questions: Any) -> list[dict[str, Any]]:
        if not isinstance(questions, list) or not 1 <= len(questions) <= 4:
            raise InteractionError("questions must contain between 1 and 4 items")
        normalized: list[dict[str, Any]] = []
        question_ids: set[str] = set()
        for question in questions:
            if not isinstance(question, dict):
                raise InteractionError("each question must be an object")
            question_id = question.get("id")
            text = question.get("question")
            options = question.get("options")
            if not isinstance(question_id, str) or not question_id.strip():
                raise InteractionError("each question needs a non-empty string id")
            if question_id in question_ids:
                raise InteractionError(f"duplicate question id: {question_id!r}")
            question_ids.add(question_id)
            if not isinstance(text, str) or not text.strip() or len(text) > 300:
                raise InteractionError("question text must be a non-empty string of at most 300 characters")
            if not isinstance(options, list) or not 2 <= len(options) <= 4:
                raise InteractionError("each question must have between 2 and 4 options")
            option_ids: set[str] = set()
            normalized_options: list[dict[str, str]] = []
            for option in options:
                if not isinstance(option, dict):
                    raise InteractionError("each option must be an object")
                option_id = option.get("id")
                label = option.get("label")
                description = option.get("description", "")
                if not isinstance(option_id, str) or not option_id.strip():
                    raise InteractionError("each option needs a non-empty string id")
                if option_id in option_ids:
                    raise InteractionError(f"duplicate option id in question {question_id!r}: {option_id!r}")
                option_ids.add(option_id)
                if not isinstance(label, str) or not label.strip() or len(label) > 80:
                    raise InteractionError("option labels must be non-empty strings of at most 80 characters")
                if not isinstance(description, str) or len(description) > 200:
                    raise InteractionError("option descriptions must be strings of at most 200 characters")
                normalized_option: dict[str, str] = {"id": option_id, "label": label}
                if "description" in option:
                    normalized_option["description"] = description
                normalized_options.append(normalized_option)
            allow_custom = question.get("allow_custom", True)
            if not isinstance(allow_custom, bool):
                raise InteractionError("allow_custom must be a boolean")
            normalized.append(
                {
                    "id": question_id,
                    "question": text,
                    "options": normalized_options,
                    "allow_custom": allow_custom,
                }
            )
        return normalized

    @staticmethod
    def _normalize_answers(
        questions: list[dict[str, Any]], answers: Any
    ) -> dict[str, dict[str, str]]:
        if not isinstance(answers, dict):
            raise InteractionError("answers must be an object")
        expected = {question["id"] for question in questions}
        if set(answers) != expected:
            missing = sorted(expected - set(answers))
            extra = sorted(set(answers) - expected)
            raise InteractionError(f"answers must match the question ids (missing={missing}, extra={extra})")

        normalized: dict[str, dict[str, str]] = {}
        for question in questions:
            question_id = question["id"]
            raw = answers[question_id]
            if isinstance(raw, str):
                option_id = raw
                custom_text = ""
            elif isinstance(raw, dict):
                option_id = raw.get("option_id")
                custom_text = raw.get("custom_text", raw.get("custom", ""))
                if option_id in (None, "") and isinstance(custom_text, str) and custom_text.strip():
                    option_id = "custom"
            else:
                raise InteractionError(f"answer for {question_id!r} must be a string or object")

            if not isinstance(custom_text, str):
                raise InteractionError(f"custom_text for {question_id!r} must be a string")
            if len(custom_text) > 1000:
                raise InteractionError(f"custom_text for {question_id!r} is too long")
            allow_custom = question.get("allow_custom", True)
            option_ids = {option["id"] for option in question["options"]}
            if option_id == "custom":
                if not allow_custom or not custom_text.strip():
                    raise InteractionError(f"a custom answer is not allowed or is empty for {question_id!r}")
            elif not isinstance(option_id, str) or option_id not in option_ids:
                raise InteractionError(f"unknown option id {option_id!r} for question {question_id!r}")
            if custom_text.strip() and not allow_custom:
                raise InteractionError(f"custom text is not allowed for question {question_id!r}")
            normalized[question_id] = {
                "option_id": option_id,
                "custom_text": custom_text.strip(),
            }
        return normalized

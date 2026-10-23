"""Internal legacy JSON-RPC 0.1 ACP frontend over the canonical Commands API."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import sys
from itertools import islice
from typing import Any

from harness.kernel.sanitizer import sanitize_output
from harness.kernel.validators import validate_session_id

from .protocol import ACPNotification, ACPResponse

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 16
_MAX_ACTIVE_TURNS = 16
_MAX_LINE_BYTES = 64 * 1024
_MAX_OUTPUT_CHARS = 64 * 1024
_MAX_OUTPUT_BYTES = 8 * 1024
_MAX_FIELD_CHARS = 4096
_MAX_PAYLOAD_BYTES = 16 * 1024
_MAX_PAYLOAD_NODES = 256
_DRAIN_TIMEOUT_S = 1.0
_EVENT_TYPES = frozenset({
    "model.text.delta",
    "model.thinking.delta",
    "tool.call",
    "tool.result",
    "approval.required",
    "turn.completed",
})
_QUESTION_EVENT_TYPES = frozenset({"question.asked"})
_TRUNCATION_MARKER = "…[truncated]"


class _PayloadBudget:
    """Post-parse retained JSON budget; not a transport/input-buffer limit."""

    def __init__(self) -> None:
        self.remaining = _MAX_PAYLOAD_BYTES - 64  # Reserve the truncation flag.
        self.nodes = _MAX_PAYLOAD_NODES

    def take(self, size: int) -> None:
        if size > self.remaining:
            raise ConnectionError("ACP payload cannot be represented within its budget")
        self.remaining -= size


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii"))


def _bounded_scalar(value: Any, budget: _PayloadBudget) -> tuple[Any, bool]:
    truncated = False
    try:
        size = _encoded_size(value)
    except (TypeError, ValueError, OverflowError):
        value, truncated = "[unsupported value]", True
        size = _encoded_size(value)
    if size > budget.remaining:
        truncated = True
        if not isinstance(value, str):
            value = _TRUNCATION_MARKER
        else:
            low, high = 0, len(value)
            while low < high:
                middle = (low + high + 1) // 2
                if _encoded_size(value[:middle] + _TRUNCATION_MARKER) <= budget.remaining:
                    low = middle
                else:
                    high = middle - 1
            value = value[:low] + _TRUNCATION_MARKER
        size = _encoded_size(value)
    budget.take(size)
    return value, truncated


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return ACPResponse(id=req_id, error={"code": code, "message": message}).to_dict()


def _safe_value(
    value: Any,
    secret_values: tuple[str, ...] = (),
    depth: int = 0,
    budget: _PayloadBudget | None = None,
) -> tuple[Any, bool]:
    """Bound and redact values crossing the trusted kernel/untrusted stdio boundary."""
    if budget is None:
        budget = _PayloadBudget()
    budget.nodes -= 1
    if depth > 5 or budget.nodes < 0:
        safe, _ = _bounded_scalar(_TRUNCATION_MARKER, budget)
        return safe, True
    if value is None or isinstance(value, (bool, int)):
        return _bounded_scalar(value, budget)
    if isinstance(value, float):
        safe, truncated = _bounded_scalar(value if math.isfinite(value) else "[unsupported value]", budget)
        return safe, truncated or not math.isfinite(value)
    if isinstance(value, str):
        if len(value) > _MAX_FIELD_CHARS:
            # Do not scan/copy an unbounded source or preserve a partial secret
            # across a prefix cut. Oversized fields are omitted in full.
            safe, _ = _bounded_scalar(_TRUNCATION_MARKER, budget)
            return safe, True
        safe = sanitize_output(value, secret_values)
        if len(safe) > _MAX_FIELD_CHARS:
            safe = safe[: _MAX_FIELD_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
            safe, _ = _bounded_scalar(safe, budget)
            return safe, True
        return _bounded_scalar(safe, budget)
    if isinstance(value, dict):
        budget.take(2)  # Braces; separators and keys are charged as added.
        safe: dict[str, Any] = {}
        truncated = len(value) > 32
        for key, item in islice(value.items(), 32):
            if not isinstance(key, str):
                truncated = True
                continue
            if len(key) > 128:
                truncated = True
                continue
            clean_key = sanitize_output(key, secret_values)[:128]
            if len(key) > 128 or clean_key != key:
                truncated = True
            overhead = _encoded_size(clean_key) + 1 + int(bool(safe))
            if budget.remaining < overhead + 64 or budget.nodes <= 0:
                truncated = True
                break
            budget.take(overhead)
            safe_item, item_truncated = _safe_value(item, secret_values, depth + 1, budget)
            safe[clean_key] = safe_item
            truncated = truncated or item_truncated
        return safe, truncated
    if isinstance(value, (list, tuple)):
        budget.take(2)
        safe_items = []
        truncated = len(value) > 32
        for item in islice(value, 32):
            overhead = int(bool(safe_items))
            if budget.remaining < overhead + 64 or budget.nodes <= 0:
                truncated = True
                break
            budget.take(overhead)
            safe_item, item_truncated = _safe_value(item, secret_values, depth + 1, budget)
            safe_items.append(safe_item)
            truncated = truncated or item_truncated
        return safe_items, truncated
    safe, _ = _bounded_scalar("[unsupported value]", budget)
    return safe, True


class ACPServer:
    """One stdio connection bound to only sessions it explicitly creates."""

    def __init__(self, commands: Any, bus: Any | None = None) -> None:
        self.commands = commands
        self.bus = bus
        self._writer: asyncio.StreamWriter | Any | None = None
        self._bus_task: asyncio.Task[None] | None = None
        self._subscriber = None
        self._owned_sessions: set[str] = set()
        self._active_turns: dict[str, asyncio.Task[Any]] = {}
        self._question_subscribers: dict[str, Any] = {}
        self._question_tasks: dict[str, asyncio.Task[None]] = {}
        self._connected = False
        self._ready = asyncio.Event()
        self._bus_ready = asyncio.Event()
        self._write_lock = asyncio.Lock()

    def _secret_values(self) -> tuple[str, ...]:
        secrets = getattr(self.commands, "secrets", None)
        # Missing kernel dependencies are permitted only for minimal test peers;
        # discovery failure on a real runtime must fail closed, not emit secrets.
        return secrets.redaction_values() if secrets is not None else ()

    def _safe_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        safe, truncated = _safe_value(payload, self._secret_values())
        assert isinstance(safe, dict)
        for field in ("session_id", "request_id", "call_id", "turn_id"):
            if field in payload and safe.get(field) != payload[field]:
                raise ConnectionError("ACP interaction identity cannot be safely represented")
        if truncated:
            safe["truncated"] = True
        return safe

    def _subscribe_questions(self, session_id: str) -> None:
        if self.bus is None or session_id in self._question_subscribers:
            return
        subscriber = self.bus.subscribe(
            types=_QUESTION_EVENT_TYPES,
            interactive=True,
            max_queue_size=64,
            session_id=session_id,
        )
        self._question_subscribers[session_id] = subscriber
        self._question_tasks[session_id] = asyncio.create_task(
            self._forward_session_questions(session_id, subscriber)
        )

    async def _forward_session_questions(self, session_id: str, subscriber: Any) -> None:
        try:
            while self._writer is not None and not self._writer.is_closing():
                event = await subscriber.get()
                if event.session_id != session_id or event.type != "question.asked":
                    continue
                data = event.data
                if not isinstance(data, dict) or not isinstance(data.get("interaction_id"), str) or not data["interaction_id"]:
                    raise ConnectionError("ACP owned question has an invalid interaction identifier")
                questions = data.get("questions")
                if not isinstance(questions, list) or not questions:
                    raise ConnectionError("ACP owned question cannot be represented")
                raw_payload = {
                    "session_id": session_id,
                    "request_id": data["interaction_id"],
                    "questions": questions,
                    "timeout_s": data.get("timeout_s"),
                }
                payload = self._safe_payload(raw_payload)
                if payload.get("truncated"):
                    raise ConnectionError("ACP question exceeds the supported delivery budget")
                original_ids = [(q.get("id"), [o.get("id") for o in q.get("options", [])]) for q in questions]
                delivered_ids = [(q.get("id"), [o.get("id") for o in q.get("options", [])]) for q in payload["questions"]]
                if original_ids != delivered_ids:
                    raise ConnectionError("ACP question identifiers cannot be safely represented")
                await self._write(ACPNotification(method="question/request", params=payload).to_dict())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ACP question forwarding failed")
            raise

    async def handle_request(self, req: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one internal legacy-wire request through actual kernel Commands."""
        if not isinstance(req, dict):
            return _error(None, -32600, "Invalid request")
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})
        if req.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(req_id, -32600, "Invalid request") if req_id is not None else None
        if "id" in req and (
            isinstance(req_id, bool)
            or not isinstance(req_id, (str, int))
            or (isinstance(req_id, str) and len(req_id) > 128)
            or (isinstance(req_id, int) and abs(req_id) > 2**63 - 1)
        ):
            return _error(None, -32600, "Invalid request id")
        if req_id is None and method != "notifications/initialized":
            return None
        if not isinstance(params, dict):
            return _error(req_id, -32602, "Request params must be an object") if req_id is not None else None

        if method == "initialize":
            return ACPResponse(id=req_id, result={
                "protocolVersion": "0.1",
                "capabilities": {
                    "streaming": True,
                    "approvals": self.bus is not None,
                    "interrupt": True,
                    "questions": self.bus is not None,
                },
                "serverInfo": {"name": "harness-zero-acp", "version": "2.0.0"},
            }).to_dict()
        if method == "notifications/initialized":
            return None
        if method == "session/new":
            if len(self._owned_sessions) >= _MAX_SESSIONS:
                return _error(req_id, -32000, "Connection session limit reached")
            session_id = params.get("session_id")
            title = params.get("title", "ACP Editor Session")
            if session_id is not None and (not isinstance(session_id, str) or not session_id):
                return _error(req_id, -32602, "session_id must be a non-empty string")
            if not isinstance(title, str):
                return _error(req_id, -32602, "title must be a string")
            if session_id is not None:
                try:
                    validate_session_id(session_id)
                except ValueError:
                    return _error(req_id, -32602, "session_id is invalid")
            try:
                if session_id is not None:
                    existing = await self.commands.sessions_list()
                    if any(
                        isinstance(row, dict) and row.get("session_id") == session_id
                        for row in existing
                    ):
                        return _error(req_id, -32001, "Requested session ID already exists")
                result = await self.commands.sessions_create(
                    session_id=session_id,
                    metadata={"title": title[:_MAX_FIELD_CHARS]},
                )
                owned_id = result.get("session_id") if isinstance(result, dict) else None
                if not isinstance(owned_id, str) or not owned_id or (session_id is not None and owned_id != session_id):
                    return _error(req_id, -32000, "Kernel returned an invalid session")
                self._owned_sessions.add(owned_id)
                if self._connected:
                    self._subscribe_questions(owned_id)
                return ACPResponse(id=req_id, result=result).to_dict()
            except Exception:
                logger.exception("ACP session creation failed")
                return _error(req_id, -32000, "Failed to create session")

        if method == "session/list":
            try:
                rows = await self.commands.sessions_list()
                return ACPResponse(
                    id=req_id,
                    result=[row for row in rows if row.get("session_id") in self._owned_sessions],
                ).to_dict()
            except Exception:
                logger.exception("ACP session listing failed")
                return _error(req_id, -32000, "Failed to list sessions")

        if method == "prompt":
            session_id, prompt, options, error = self._prompt_args(req_id, params)
            if error:
                return error
            assert session_id is not None and prompt is not None
            try:
                result = await self._execute_prompt(session_id, prompt, options)
                return ACPResponse(id=req_id, result=result).to_dict()
            except Exception:
                logger.exception("ACP turn execution failed")
                return _error(req_id, -32000, "Turn execution failed")

        if method == "cancel":
            session_id = params.get("session_id")
            if not isinstance(session_id, str) or session_id not in self._owned_sessions:
                return _error(req_id, -32001, "Unknown or unowned session")
            active = self._active_turns.get(session_id)
            if active is None or active.done():
                return ACPResponse(id=req_id, result={"status": "not_active", "session_id": session_id}).to_dict()
            try:
                accepted = await self.commands.cancel_turn(session_id=session_id)
                return ACPResponse(id=req_id, result={
                    "status": "cancellation_requested" if accepted else "not_active",
                    "session_id": session_id,
                }).to_dict()
            except Exception:
                logger.exception("ACP cancellation failed")
                return _error(req_id, -32000, "Failed to request cancellation")

        if method in ("approval/resolve", "permission/resolve"):
            return await self._resolve_approval(req_id, params)

        if method == "question/answer":
            return await self._answer_question(req_id, params)

        return _error(req_id, -32601, f"Method '{method[:128]}' not found") if req_id is not None else None

    def _prompt_args(
        self, req_id: Any, params: dict[str, Any]
    ) -> tuple[str | None, str | None, dict[str, str], dict[str, Any] | None]:
        session_id = params.get("session_id")
        prompt = params.get("prompt")
        options = params.get("options", {})
        if not isinstance(session_id, str) or session_id not in self._owned_sessions:
            return None, None, {}, _error(req_id, -32001, "Unknown or unowned session")
        if not isinstance(prompt, str) or not prompt.strip():
            return None, None, {}, _error(req_id, -32602, "prompt must be a non-empty string")
        if len(prompt) > _MAX_OUTPUT_CHARS:
            return None, None, {}, _error(req_id, -32602, "prompt exceeds the input limit")
        if not isinstance(options, dict) or any(
            key not in {"provider", "model", "strategy"} or not isinstance(value, str)
            for key, value in options.items()
        ):
            return None, None, {}, _error(req_id, -32602, "options may contain only string provider, model, and strategy")
        if session_id in self._active_turns and not self._active_turns[session_id].done():
            return None, None, {}, _error(req_id, -32000, "Session already has an active turn")
        if len(self._active_turns) >= _MAX_ACTIVE_TURNS:
            return None, None, {}, _error(req_id, -32000, "Connection active-turn limit reached")
        return session_id, prompt, dict(options), None

    async def _execute_prompt(self, session_id: str, prompt: str, options: dict[str, str]) -> dict[str, Any]:
        task = asyncio.current_task()
        assert task is not None
        self._active_turns[session_id] = task
        try:
            result = await self.commands.submit_turn(
                session_id=session_id,
                prompt=prompt,
                options=options or None,
            )
            stop_reason = getattr(result, "stop_reason", "")
            output = getattr(result, "output", "")
            if not isinstance(output, str):
                raise TypeError("kernel returned non-text turn output")
            source_truncated = len(output) > _MAX_OUTPUT_BYTES
            if source_truncated:
                # Only inspect a bounded preview, and emit no source prefix: a
                # cut can bisect a credential which the sanitizer cannot match.
                # Retain the fact of any redaction detected in that preview.
                preview = output[:_MAX_OUTPUT_BYTES]
                masked = sanitize_output(preview, self._secret_values())
                output = ("[REDACTED SECRET]" if masked != preview else "") + _TRUNCATION_MARKER
            else:
                output = sanitize_output(output, self._secret_values())
            output = output.encode("utf-8", errors="replace")
            output_truncated = source_truncated or len(output) > _MAX_OUTPUT_BYTES
            if len(output) > _MAX_OUTPUT_BYTES:
                marker = _TRUNCATION_MARKER.encode("utf-8")
                output = output[: _MAX_OUTPUT_BYTES - len(marker)] + marker
            output = output.decode("utf-8", errors="ignore")
            if stop_reason == "cancelled":
                status = "cancelled"
            elif getattr(result, "error", None):
                status = "error"
            else:
                status = "completed"
            response = {
                "status": status,
                "output": output,
                "session_id": session_id,
                "stop_reason": stop_reason,
            }
            if output_truncated:
                response["output_truncated"] = True
            error = getattr(result, "error", None)
            if error:
                response["error"] = _safe_value(str(error), self._secret_values())[0]
            return response
        finally:
            if self._active_turns.get(session_id) is task:
                self._active_turns.pop(session_id, None)

    async def _resolve_approval(self, req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        approval_id = params.get("request_id", params.get("approval_id"))
        if not isinstance(approval_id, str) or not approval_id:
            return _error(req_id, -32602, "request_id must be a non-empty string")
        if "approved" in params:
            approved = params["approved"]
            if not isinstance(approved, bool):
                return _error(req_id, -32602, "approved must be a boolean")
        else:
            decision = params.get("decision")
            if decision not in ("approve", "deny"):
                return _error(req_id, -32602, "decision must be 'approve' or 'deny'")
            approved = decision == "approve"
        reason = params.get("reason", "")
        if not isinstance(reason, str):
            return _error(req_id, -32602, "reason must be a string")
        broker = getattr(getattr(self.commands, "gate", None), "approvals", None)
        request = broker.peek(approval_id) if broker is not None else None
        if request is None or request.session_id not in self._owned_sessions:
            return _error(req_id, -32001, "Unknown or unowned approval")
        try:
            result = await self.commands.resolve_approval(
                approval_id=approval_id,
                approved=approved,
                reason=reason[:_MAX_FIELD_CHARS],
            )
            if not isinstance(result, dict) or result.get("status") != "resolved":
                return _error(req_id, -32001, "Approval is no longer pending")
            return ACPResponse(id=req_id, result=result).to_dict()
        except Exception:
            logger.exception("ACP approval resolution failed")
            return _error(req_id, -32000, "Failed to resolve approval")

    async def _answer_question(self, req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("session_id")
        request_id = params.get("request_id", params.get("interaction_id"))
        answers = params.get("answers")
        if not isinstance(session_id, str) or session_id not in self._owned_sessions:
            return _error(req_id, -32001, "Unknown or unowned session")
        if not isinstance(request_id, str) or not request_id:
            return _error(req_id, -32602, "request_id must be a non-empty string")
        if not isinstance(answers, dict):
            return _error(req_id, -32602, "answers must be an object")
        pending = await self.commands.interactions_list(session_id=session_id)
        if not any(row.get("interaction_id") == request_id for row in pending if isinstance(row, dict)):
            return _error(req_id, -32001, "Unknown or unowned question")
        try:
            result = await self.commands.question_answer(
                interaction_id=request_id,
                answers=answers,
                by="acp",
            )
            if not isinstance(result, dict) or result.get("status") != "answered":
                return _error(req_id, -32001, "Question is no longer pending")
            return ACPResponse(id=req_id, result=result).to_dict()
        except Exception:
            logger.exception("ACP question resolution failed")
            return _error(req_id, -32602, "Invalid question answer")

    def _abort_output(self, writer: Any) -> None:
        self._connected = False
        transport = getattr(writer, "transport", None)
        abort = getattr(transport, "abort", None)
        close = getattr(writer, "close", None)
        if callable(abort):
            abort()
        elif callable(close):
            close()

    async def _write(self, message: dict[str, Any]) -> None:
        writer = self._writer
        if writer is None or writer.is_closing():
            self._abort_output(writer)
            raise ConnectionError("ACP output channel is closed")
        data = json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(data) > _MAX_LINE_BYTES:
            if "id" not in message:
                raise ConnectionError("ACP notification exceeds the supported output limit")
            data = json.dumps(
                _error(message.get("id"), -32000, "Response exceeds output limit"),
                separators=(",", ":"),
            ).encode("ascii") + b"\n"
        async with self._write_lock:
            try:
                if self._writer is not writer or writer.is_closing():
                    raise ConnectionError("ACP output channel is closed")
                writer.write(data)
                async with asyncio.timeout(_DRAIN_TIMEOUT_S):
                    await writer.drain()
            except BaseException:
                # Abort while still holding the lock: queued writes must not
                # each spend another drain timeout before the supervisor wakes.
                self._abort_output(writer)
                raise

    async def _forward_bus_events(self) -> None:
        """Forward only allowlisted, typed SDK events for this connection's sessions."""
        if not self.bus or not self._writer:
            return
        subscriber = self.bus.subscribe(
            types=_EVENT_TYPES,
            interactive=False,
            max_queue_size=128,
        )
        self._subscriber = subscriber
        self._bus_ready.set()
        try:
            while self._writer is not None and not self._writer.is_closing():
                event = await subscriber.get()
                if event.session_id not in self._owned_sessions:
                    continue
                data = event.data
                if not isinstance(data, dict):
                    if event.type == "approval.required":
                        raise ConnectionError("ACP owned approval cannot be represented")
                    continue
                params: dict[str, Any]
                method: str
                if event.type == "model.text.delta" and isinstance(data.get("text"), str):
                    method, params = "turn/delta", {"session_id": event.session_id, "delta": data["text"]}
                elif event.type == "model.thinking.delta" and isinstance(data.get("text"), str):
                    method, params = "turn/thinking", {"session_id": event.session_id, "delta": data["text"]}
                elif event.type == "tool.call" and isinstance(data.get("call_id"), str) and isinstance(data.get("name"), str):
                    method, params = "turn/tool_call", {
                        "session_id": event.session_id,
                        "call_id": data["call_id"],
                        "name": data["name"],
                        "arguments": data.get("arguments", {}),
                    }
                elif event.type == "tool.result" and isinstance(data.get("call_id"), str):
                    method, params = "turn/tool_result", {
                        "session_id": event.session_id,
                        "call_id": data["call_id"],
                        "is_error": data.get("is_error") if isinstance(data.get("is_error"), bool) else False,
                    }
                elif event.type == "approval.required":
                    if not isinstance(data.get("approval_id"), str) or not data["approval_id"]:
                        raise ConnectionError("ACP owned approval has an invalid interaction identifier")
                    method, params = "permission/request", {
                        "session_id": event.session_id,
                        "request_id": data["approval_id"],
                        "kind": data.get("kind", "approval"),
                        "summary": data.get("summary", ""),
                        "details": data.get("details", {}),
                    }
                elif event.type == "turn.completed":
                    method, params = "turn/completed", {
                        "session_id": event.session_id,
                        "turn_id": event.turn_id,
                        "stop_reason": data.get("stop_reason", ""),
                    }
                else:
                    continue
                await self._write(ACPNotification(method=method, params=self._safe_payload(params)).to_dict())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ACP event forwarding failed")
            raise
        finally:
            self.bus.unsubscribe(subscriber)
            if self._subscriber is subscriber:
                self._subscriber = None

    async def _handle_line(self, line: bytes) -> tuple[dict[str, Any] | None, asyncio.Task[Any] | None]:
        if len(line) > _MAX_LINE_BYTES:
            return _error(None, -32700, "Request line exceeds limit"), None
        try:
            request = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error(None, -32700, "Parse error"), None
        if not isinstance(request, dict):
            return _error(None, -32600, "Invalid request"), None
        if request.get("method") == "prompt" and request.get("id") is not None:
            if len(self._active_turns) >= _MAX_ACTIVE_TURNS:
                return _error(request.get("id"), -32000, "Connection active-turn limit reached"), None
            task = asyncio.create_task(self.handle_request(request))
            return None, task
        return await self.handle_request(request), None

    async def run_stdio(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one bounded connection; prompt tasks do not block cancel/approval requests."""
        self._writer = writer
        self._connected = True
        reader_task: asyncio.Task[bytes] | None = None
        turns: dict[asyncio.Task[Any], Any] = {}
        try:
            if writer.is_closing():
                raise ConnectionError("ACP output channel is closed")
            for session_id in tuple(self._owned_sessions):
                self._subscribe_questions(session_id)
            self._ready.set()
            if self.bus:
                self._bus_task = asyncio.create_task(self._forward_bus_events())
            while True:
                if reader_task is None:
                    reader_task = asyncio.create_task(reader.readline())
                forwarders = {task for task in (self._bus_task, *self._question_tasks.values()) if task is not None}
                done, _ = await asyncio.wait(
                    {reader_task, *turns.keys(), *forwarders},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if writer.is_closing():
                    raise ConnectionError("ACP output channel is closed")
                for task in forwarders:
                    if task in done:
                        if task.cancelled():
                            raise ConnectionError("ACP notification forwarding was cancelled")
                        error = task.exception()
                        if error is not None:
                            raise error
                        raise ConnectionError("ACP notification forwarding stopped unexpectedly")
                # Turn responses are emitted as soon as they complete, even when no new input arrives.
                for task in list(turns):
                    if task in done:
                        turns.pop(task, None)
                        try:
                            response = task.result()
                        except Exception:
                            response = _error(None, -32000, "Turn execution failed")
                        if response is not None:
                            await self._write(response)
                if reader_task not in done:
                    continue
                try:
                    line = reader_task.result()
                except ValueError:
                    await self._write(_error(None, -32700, "Request line exceeds limit"))
                    break
                reader_task = None
                if not line:
                    break
                try:
                    request = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    await self._write(_error(None, -32700, "Parse error"))
                    continue
                if (
                    isinstance(request, dict)
                    and request.get("method") == "prompt"
                    and request.get("id") is not None
                    and len(turns) >= _MAX_ACTIVE_TURNS
                ):
                    await self._write(_error(request.get("id"), -32000, "Connection active-turn limit reached"))
                    continue
                response, task = await self._handle_line(line)
                if task is not None:
                    turns[task] = None
                elif response is not None and isinstance(response, dict) and response.get("id") is not None:
                    await self._write(response)
        except BaseException:
            # A live protocol connection without its notification channel must
            # not admit more turns or retain interactive authority.
            self._abort_output(writer)
            raise
        finally:
            if reader_task is not None:
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
            # Ask the runner to stop each connection-owned turn before awaiting its cleanup.
            for session_id, task in list(self._active_turns.items()):
                if task in turns:
                    try:
                        await self.commands.cancel_turn(session_id=session_id)
                    except Exception:
                        logger.exception("ACP EOF cancellation failed")
            if turns:
                pending = asyncio.gather(*turns, return_exceptions=True)
                try:
                    await asyncio.wait_for(pending, timeout=2.0)
                except TimeoutError:
                    for task in turns:
                        task.cancel()
                    await asyncio.gather(*turns, return_exceptions=True)
            if self._bus_task is not None:
                self._bus_task.cancel()
                await asyncio.gather(self._bus_task, return_exceptions=True)
                self._bus_task = None
            self._connected = False
            question_tasks = list(self._question_tasks.values())
            for task in question_tasks:
                task.cancel()
            if question_tasks:
                await asyncio.gather(*question_tasks, return_exceptions=True)
            for subscriber in self._question_subscribers.values():
                self.bus.unsubscribe(subscriber)
            self._question_tasks.clear()
            self._question_subscribers.clear()
            self._writer = None


async def run_acp_server_from_stdio(commands: Any, bus: Any | None = None) -> None:
    """Helper to start the internal legacy server listening over standard input/output."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=_MAX_LINE_BYTES)
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    await ACPServer(commands=commands, bus=bus).run_stdio(reader, writer)

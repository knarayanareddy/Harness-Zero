"""Internal legacy JSON-RPC 0.1 ACP frontend over the canonical Commands API."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import sys
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from harness.sdk.sanitizer import sanitize_output
from harness.sdk.validators import validate_session_id

from .protocol import ACPNotification, ACPRequest, ACPResponse

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 16
_MAX_ACTIVE_TURNS = 16
_MAX_PENDING_PERMISSIONS = 16
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
    "model.completed",
    "turn.started",
    "turn.cancelled",
    "turn.error",
    "tool.call",
    "tool.result",
    "approval.required",
    "turn.completed",
})
_QUESTION_EVENT_TYPES = frozenset({"question.asked"})
_TRUNCATION_MARKER = "…[truncated]"


@dataclass(slots=True)
class _PendingV1Permission:
    rpc_id: str
    session_id: str
    approval_id: str
    call_id: str
    turn_id: str
    deadline: float
    future: asyncio.Future[tuple[bool, str]]


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


def _finite_deadline(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


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
        self._v1_turn_barriers: dict[str, tuple[asyncio.Future[str], str | None]] = {}
        self._v1_cancel_requested: set[str] = set()
        self._v1_tool_calls: dict[tuple[str, str], str] = {}
        self._v1_approval_subscribers: dict[str, Any] = {}
        self._v1_permission_tasks: set[asyncio.Task[None]] = set()
        self._v1_pending_permissions: dict[str, _PendingV1Permission] = {}
        self._v1_permission_counter = 0
        self._question_subscribers: dict[str, Any] = {}
        self._question_tasks: dict[str, asyncio.Task[None]] = {}
        self._connected = False
        self._closing = False
        self._dialect: str | None = None
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
        for field in (
            "session_id", "request_id", "call_id", "turn_id", "sessionId", "toolCallId",
        ):
            if field in payload and safe.get(field) != payload[field]:
                raise ConnectionError("ACP interaction identity cannot be safely represented")
        if truncated:
            safe["truncated"] = True
        return safe

    def _initialize(self, req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        has_version = "protocolVersion" in params
        version = params.get("protocolVersion")
        if has_version and (isinstance(version, bool) or not isinstance(version, int)):
            return _error(req_id, -32602, "protocolVersion must be an integer")
        if self._dialect == "legacy" and has_version:
            return _error(req_id, -32600, "Connection is already using the legacy protocol")
        if self._dialect == "v1" and not has_version:
            return _error(req_id, -32600, "Connection is already using ACP v1")
        if not has_version:
            self._dialect = "legacy"
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

        self._dialect = "v1"
        return ACPResponse(id=req_id, result={
            "protocolVersion": 1,
            "agentCapabilities": {},
            "authMethods": [],
            "agentInfo": {"name": "harness-zero-acp", "version": "2.0.0"},
        }).to_dict()

    def _valid_v1_cwd(self, cwd: Any) -> bool:
        if not isinstance(cwd, str) or not cwd or not Path(cwd).is_absolute():
            return False
        try:
            return Path(cwd).resolve(strict=True) == Path(self.commands.workspace).resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False

    async def _v1_new_session(self, req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        allowed = {"cwd", "mcpServers", "additionalDirectories", "_meta"}
        if set(params) - allowed:
            return _error(req_id, -32602, "Unsupported session/new option")
        if not self._valid_v1_cwd(params.get("cwd")):
            return _error(req_id, -32602, "cwd must resolve to the launched workspace")
        servers = params.get("mcpServers")
        if not isinstance(servers, list) or servers:
            return _error(req_id, -32602, "Client MCP servers are not supported")
        directories = params.get("additionalDirectories", [])
        if directories not in (None, []) or (directories is not None and not isinstance(directories, list)):
            return _error(req_id, -32602, "Additional directories are not supported")
        if "_meta" in params and not isinstance(params["_meta"], dict):
            return _error(req_id, -32602, "_meta must be an object")
        if len(self._owned_sessions) >= _MAX_SESSIONS:
            return _error(req_id, -32000, "Connection session limit reached")
        try:
            result = await self.commands.sessions_create(metadata={"title": "ACP Editor Session"})
            session_id = result.get("session_id") if isinstance(result, dict) else None
            if not isinstance(session_id, str) or not session_id:
                return _error(req_id, -32000, "Kernel returned an invalid session")
            validate_session_id(session_id)
            self._owned_sessions.add(session_id)
            if self._connected:
                self._subscribe_v1_approvals(session_id)
            return ACPResponse(id=req_id, result={"sessionId": session_id}).to_dict()
        except Exception:
            logger.exception("ACP v1 session creation failed")
            return _error(req_id, -32000, "Failed to create session")

    async def _v1_list_sessions(self, req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        if set(params) - {"cwd", "cursor", "_meta"}:
            return _error(req_id, -32602, "Unsupported session/list option")
        if params.get("cwd") is not None and not self._valid_v1_cwd(params["cwd"]):
            return _error(req_id, -32602, "cwd must resolve to the launched workspace")
        if params.get("cursor") not in (None, ""):
            return _error(req_id, -32602, "Session-list cursors are not supported")
        if "_meta" in params and not isinstance(params["_meta"], dict):
            return _error(req_id, -32602, "_meta must be an object")
        try:
            rows = await self.commands.sessions_list()
            sessions = []
            for row in rows:
                if not isinstance(row, dict) or row.get("session_id") not in self._owned_sessions:
                    continue
                session_id = row.get("session_id")
                workspace = row.get("workspace")
                if not isinstance(session_id, str) or not isinstance(workspace, str):
                    return _error(req_id, -32000, "Kernel returned invalid session metadata")
                try:
                    validate_session_id(session_id)
                except ValueError:
                    return _error(req_id, -32000, "Kernel returned an invalid session")
                item: dict[str, Any] = {"sessionId": session_id, "cwd": workspace}
                title = row.get("title")
                if isinstance(title, str) and title:
                    item["title"] = title
                sessions.append(item)
            return ACPResponse(id=req_id, result={"sessions": sessions}).to_dict()
        except Exception:
            logger.exception("ACP v1 session listing failed")
            return _error(req_id, -32000, "Failed to list sessions")

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

    def _subscribe_v1_approvals(self, session_id: str) -> None:
        if self.bus is None or session_id in self._v1_approval_subscribers:
            return
        self._v1_approval_subscribers[session_id] = self.bus.subscribe(
            types=(),
            interactive=True,
            max_queue_size=1,
            session_id=session_id,
            interaction_kinds={"approval"},
        )

    def _revoke_v1_approval_authority(self) -> None:
        if self.bus is not None:
            for subscriber in self._v1_approval_subscribers.values():
                self.bus.unsubscribe(subscriber)
        self._v1_approval_subscribers.clear()

    def _next_v1_permission_id(self) -> str:
        for _ in range(_MAX_PENDING_PERMISSIONS + 1):
            self._v1_permission_counter = (self._v1_permission_counter + 1) % (2**64)
            if self._v1_permission_counter == 0:
                self._v1_permission_counter = 1
            rpc_id = f"harness-permission-{self._v1_permission_counter:016x}"
            if rpc_id not in self._v1_pending_permissions:
                return rpc_id
        raise ConnectionError("ACP permission request capacity is exhausted")

    def _receive_v1_permission_response(self, response: dict[str, Any]) -> None:
        rpc_id = response.get("id")
        if not isinstance(rpc_id, str) or len(rpc_id) > 64:
            return
        pending = self._v1_pending_permissions.get(rpc_id)
        if pending is None or pending.future.done():
            return
        if not _finite_deadline(pending.deadline) or asyncio.get_running_loop().time() >= pending.deadline:
            return

        decision = (False, "ACP permission response was invalid or unavailable")
        if response.get("jsonrpc") == "2.0":
            if set(response) == {"jsonrpc", "id", "error"}:
                decision = (False, "ACP client could not handle the permission request")
            elif set(response) == {"jsonrpc", "id", "result"}:
                result = response.get("result")
                if isinstance(result, dict) and set(result) <= {"outcome", "_meta"}:
                    metadata = result.get("_meta")
                    outcome = result.get("outcome")
                    if ("_meta" not in result or isinstance(metadata, dict)) and isinstance(outcome, dict):
                        if set(outcome) <= {"outcome", "optionId", "_meta"}:
                            outcome_meta = outcome.get("_meta")
                            if "_meta" not in outcome or isinstance(outcome_meta, dict):
                                if outcome.get("outcome") == "cancelled" and set(outcome) <= {"outcome", "_meta"}:
                                    decision = (False, "ACP client cancelled the permission request")
                                elif outcome.get("outcome") == "selected":
                                    selected = outcome.get("optionId")
                                    if selected == "allow_once" and set(outcome) <= {"outcome", "optionId", "_meta"}:
                                        decision = (True, "Approved once by ACP client")
                                    elif selected == "reject_once" and set(outcome) <= {"outcome", "optionId", "_meta"}:
                                        decision = (False, "Rejected once by ACP client")
        pending.future.set_result(decision)

    async def _resolve_v1_approval(
        self, *, approval_id: str, session_id: str, call_id: str | None,
        approved: bool, reason: str,
    ) -> None:
        broker = getattr(getattr(self.commands, "gate", None), "approvals", None)
        request = broker.peek(approval_id) if broker is not None else None
        if (
            request is None
            or request.session_id != session_id
            or request.call_id != call_id
            or session_id not in self._owned_sessions
        ):
            return
        await self.commands.resolve_approval(
            approval_id=approval_id,
            approved=approved,
            by="acp",
            reason=reason,
        )

    def _cancel_v1_permissions(self, session_id: str | None, reason: str) -> None:
        for rpc_id, pending in tuple(self._v1_pending_permissions.items()):
            if session_id is not None and pending.session_id != session_id:
                continue
            self._v1_pending_permissions.pop(rpc_id, None)
            if not pending.future.done():
                pending.future.set_result((False, reason))

    def _schedule_v1_approval(self, event: Any, data: dict[str, Any]) -> None:
        if len(self._v1_permission_tasks) >= _MAX_PENDING_PERMISSIONS:
            raise ConnectionError("ACP permission request capacity is exhausted")
        task = asyncio.create_task(self._forward_v1_approval(event, data))
        self._v1_permission_tasks.add(task)

    async def _forward_v1_approval(self, event: Any, data: dict[str, Any]) -> None:
        session_id = event.session_id
        approval_id = data.get("approval_id")
        call_id = data.get("call_id")
        broker = getattr(getattr(self.commands, "gate", None), "approvals", None)
        approval = broker.peek(approval_id) if broker is not None and isinstance(approval_id, str) else None
        if approval is None or approval.session_id != session_id or session_id not in self._owned_sessions:
            return
        deadline = getattr(approval, "deadline", None)
        loop = asyncio.get_running_loop()
        if not _finite_deadline(deadline) or loop.time() >= deadline:
            return
        if not isinstance(call_id, str) or not call_id or approval.call_id != call_id:
            await self._resolve_v1_approval(
                approval_id=approval.id, session_id=session_id, call_id=approval.call_id,
                approved=False, reason="ACP permission request could not be correlated",
            )
            return
        turn_id = self._v1_tool_calls.get((session_id, call_id))
        barrier = self._v1_turn_barriers.get(session_id)
        if turn_id is None or barrier is None or barrier[1] != turn_id:
            await self._resolve_v1_approval(
                approval_id=approval.id, session_id=session_id, call_id=call_id,
                approved=False, reason="ACP permission request was outside the active tool call",
            )
            return
        if len(self._v1_pending_permissions) >= _MAX_PENDING_PERMISSIONS:
            await self._resolve_v1_approval(
                approval_id=approval.id, session_id=session_id, call_id=call_id,
                approved=False, reason="ACP permission request capacity is exhausted",
            )
            return

        details = data.get("details")
        if not isinstance(details, dict) or not isinstance(details.get("arguments"), dict):
            await self._resolve_v1_approval(
                approval_id=approval.id, session_id=session_id, call_id=call_id,
                approved=False, reason="ACP permission request could not be represented",
            )
            return
        name = details.get("name")
        if not isinstance(name, str) or not name:
            name = data.get("summary", "Tool permission request")
        if not isinstance(name, str):
            name = "Tool permission request"
        tool_kind = {
            "view_file": "read",
            "write_to_file": "edit",
            "edit_file": "edit",
            "delete_file": "delete",
            "shell": "execute",
        }.get(details.get("name"), "other")
        raw_params = {
            "sessionId": session_id,
            "toolCall": {
                "toolCallId": call_id,
                "title": name,
                "kind": tool_kind,
                "status": "pending",
                "rawInput": details["arguments"],
            },
            "options": [
                {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "reject_once", "name": "Reject once", "kind": "reject_once"},
            ],
        }
        params = self._safe_payload(raw_params)
        safe_tool_call = params.get("toolCall")
        if (
            params.get("sessionId") != session_id
            or not isinstance(safe_tool_call, dict)
            or safe_tool_call.get("toolCallId") != call_id
        ):
            raise ConnectionError("ACP permission identity cannot be safely represented")
        if loop.time() >= deadline:
            return

        rpc_id = self._next_v1_permission_id()
        pending = _PendingV1Permission(
            rpc_id=rpc_id,
            session_id=session_id,
            approval_id=approval.id,
            call_id=call_id,
            turn_id=turn_id,
            deadline=deadline,
            future=loop.create_future(),
        )
        self._v1_pending_permissions[rpc_id] = pending
        try:
            await self._write(ACPRequest(
                id=rpc_id, method="session/request_permission", params=params,
            ).to_dict())
            remaining = max(0.0, pending.deadline - loop.time())
            try:
                async with asyncio.timeout(remaining):
                    approved, reason = await pending.future
            except TimeoutError:
                # The shared broker owns expiry settlement. This adapter must not
                # turn an elapsed approval into a durable ACP rejection.
                return
            await self._resolve_v1_approval(
                approval_id=pending.approval_id,
                session_id=pending.session_id,
                call_id=pending.call_id,
                approved=approved,
                reason=reason,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await self._resolve_v1_approval(
                    approval_id=pending.approval_id,
                    session_id=pending.session_id,
                    call_id=pending.call_id,
                    approved=False,
                    reason="ACP permission delivery failed",
                )
            finally:
                self._revoke_v1_approval_authority()
            raise
        finally:
            if self._v1_pending_permissions.get(rpc_id) is pending:
                self._v1_pending_permissions.pop(rpc_id, None)
            if not pending.future.done():
                pending.future.cancel()

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
        if "method" not in req:
            rpc_id = req.get("id")
            if (
                "result" in req
                or "error" in req
                or (isinstance(rpc_id, str) and rpc_id in self._v1_pending_permissions)
            ):
                self._receive_v1_permission_response(req)
                return None
            return _error(rpc_id, -32600, "Invalid request") if rpc_id is not None else None
        if req.get("jsonrpc") != "2.0":
            return _error(req.get("id"), -32600, "Invalid request") if req.get("id") is not None else None
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})
        if not isinstance(method, str):
            return _error(req_id, -32600, "Invalid request") if req_id is not None else None
        if "id" in req and (
            isinstance(req_id, bool)
            or not isinstance(req_id, (str, int))
            or (isinstance(req_id, str) and len(req_id) > 128)
            or (isinstance(req_id, int) and abs(req_id) > 2**63 - 1)
        ):
            return _error(None, -32600, "Invalid request id")
        if req_id is None and method not in ("notifications/initialized", "session/cancel"):
            return None
        if not isinstance(params, dict):
            return _error(req_id, -32602, "Request params must be an object") if req_id is not None else None

        if method == "initialize":
            return self._initialize(req_id, params)
        if method == "notifications/initialized":
            return None

        if method == "session/prompt":
            if self._dialect != "v1":
                return _error(req_id, -32002, "ACP v1 initialize is required")
            session_id, prompt, error = self._v1_prompt_args(req_id, params)
            if error:
                return error
            assert session_id is not None and prompt is not None
            task = self._schedule_v1_prompt(req_id, session_id, prompt)
            return await task

        if method == "session/cancel":
            if req_id is not None:
                return _error(req_id, -32600, "session/cancel must be a notification")
            if self._dialect != "v1":
                return None
            if set(params) - {"sessionId", "_meta"}:
                return None
            if "_meta" in params and not isinstance(params["_meta"], dict):
                return None
            session_id = params.get("sessionId")
            if not isinstance(session_id, str) or session_id not in self._owned_sessions:
                return None
            active = self._active_turns.get(session_id)
            if active is None or active.done():
                return None
            try:
                accepted = await self.commands.cancel_turn(session_id=session_id)
                if not accepted:
                    # The JSON-RPC task can arrive immediately after session/prompt,
                    # before Commands has entered the runner. Retry only when the
                    # actual turn.started producer fact confirms that lifecycle.
                    self._v1_cancel_requested.add(session_id)
                else:
                    self._cancel_v1_permissions(session_id, "ACP session was cancelled")
            except Exception:
                logger.exception("ACP v1 cancellation failed")
            return None

        if self._dialect == "v1":
            if method == "session/new":
                return await self._v1_new_session(req_id, params)
            if method == "session/list":
                return await self._v1_list_sessions(req_id, params)
            return _error(req_id, -32601, f"Method '{method[:128]}' is not supported by ACP v1")

        if method == "session/new":
            if any(key in params for key in ("cwd", "mcpServers", "additionalDirectories")):
                code = -32600 if self._dialect == "legacy" else -32002
                message = "Connection is already using the legacy protocol" if code == -32600 else "ACP v1 initialize is required"
                return _error(req_id, code, message)
            if self._dialect is None:
                self._dialect = "legacy"
        elif method == "session/list":
            if any(key in params for key in ("cwd", "cursor")):
                code = -32600 if self._dialect == "legacy" else -32002
                message = "Connection is already using the legacy protocol" if code == -32600 else "ACP v1 initialize is required"
                return _error(req_id, code, message)
            if self._dialect is None:
                self._dialect = "legacy"

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

    def _v1_prompt_args(
        self, req_id: Any, params: dict[str, Any],
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        if not isinstance(params, dict):
            return None, None, _error(req_id, -32602, "Request params must be an object")
        if set(params) - {"sessionId", "prompt", "_meta"}:
            return None, None, _error(req_id, -32602, "Unsupported session/prompt option")
        if "_meta" in params and not isinstance(params["_meta"], dict):
            return None, None, _error(req_id, -32602, "_meta must be an object")
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or session_id not in self._owned_sessions:
            return None, None, _error(req_id, -32001, "Unknown or unowned session")
        if self.bus is None:
            return None, None, _error(req_id, -32000, "ACP v1 update stream is unavailable")
        blocks = params.get("prompt")
        if not isinstance(blocks, list) or not blocks or len(blocks) > 64:
            return None, None, _error(req_id, -32602, "prompt must contain supported content blocks")

        parts: list[str] = []
        total_chars = 0
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                return None, None, _error(req_id, -32602, "Invalid prompt content block")
            kind = block["type"]
            if kind == "text":
                if set(block) - {"type", "text", "annotations", "_meta"}:
                    return None, None, _error(req_id, -32602, "Unsupported text content field")
                text = block.get("text")
                if (
                    not isinstance(text, str)
                    or ("annotations" in block and not isinstance(block["annotations"], dict))
                    or ("_meta" in block and not isinstance(block["_meta"], dict))
                ):
                    return None, None, _error(req_id, -32602, "text content must be a string")
                parts.append(text)
                total_chars += len(text)
            elif kind == "resource_link":
                if set(block) - {
                    "type", "name", "uri", "description", "title", "mimeType", "size", "annotations", "_meta",
                }:
                    return None, None, _error(req_id, -32602, "Unsupported resource_link field")
                name, uri = block.get("name"), block.get("uri")
                description = block.get("description", "")
                title = block.get("title")
                mime_type = block.get("mimeType")
                size = block.get("size")
                if (
                    not isinstance(name, str) or not name or len(name) > _MAX_FIELD_CHARS
                    or not isinstance(uri, str) or not uri or len(uri) > _MAX_FIELD_CHARS
                    or not isinstance(description, str) or len(description) > _MAX_FIELD_CHARS
                    or (title is not None and (not isinstance(title, str) or len(title) > _MAX_FIELD_CHARS))
                    or (mime_type is not None and (not isinstance(mime_type, str) or len(mime_type) > 256))
                    or (size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0))
                    or ("annotations" in block and not isinstance(block["annotations"], dict))
                    or ("_meta" in block and not isinstance(block["_meta"], dict))
                ):
                    return None, None, _error(req_id, -32602, "Invalid resource_link reference")
                reference = ["[resource_link reference]", f"name: {name}", f"uri: {uri}"]
                if title:
                    reference.append(f"title: {title}")
                if description:
                    reference.append(f"description: {description}")
                parts.append("\n".join(reference))
                total_chars += sum(map(len, reference))
            else:
                return None, None, _error(req_id, -32602, f"Unsupported prompt content type '{kind[:64]}'")
            if total_chars > _MAX_OUTPUT_CHARS:
                return None, None, _error(req_id, -32602, "prompt exceeds the input limit")

        prompt = "\n".join(parts)
        if not prompt.strip():
            return None, None, _error(req_id, -32602, "prompt must not be empty")
        if session_id in self._active_turns and not self._active_turns[session_id].done():
            return None, None, _error(req_id, -32000, "Session already has an active turn")
        if len(self._active_turns) >= _MAX_ACTIVE_TURNS:
            return None, None, _error(req_id, -32000, "Connection active-turn limit reached")
        return session_id, prompt, None

    def _schedule_v1_prompt(self, req_id: Any, session_id: str, prompt: str) -> asyncio.Task[Any]:
        loop = asyncio.get_running_loop()
        barrier: asyncio.Future[str] = loop.create_future()
        self._v1_turn_barriers[session_id] = (barrier, None)
        task = asyncio.create_task(self._execute_v1_prompt(req_id, session_id, prompt))
        self._active_turns[session_id] = task
        return task

    async def _execute_v1_prompt(self, req_id: Any, session_id: str, prompt: str) -> dict[str, Any]:
        task = asyncio.current_task()
        assert task is not None
        barrier, _ = self._v1_turn_barriers[session_id]
        try:
            await self._bus_ready.wait()
            result = await self.commands.submit_turn(session_id=session_id, prompt=prompt)
            terminal = await barrier
            if terminal == "turn.cancelled" or getattr(result, "stop_reason", None) == "cancelled":
                stop_reason = "cancelled"
            elif getattr(result, "error", None):
                return _error(req_id, -32000, "Turn execution failed")
            else:
                raw_reason = getattr(result, "stop_reason", None)
                stop_reason = {
                    "stop": "end_turn",
                    "end_turn": "end_turn",
                    "max_tokens": "max_tokens",
                    "max_iterations": "max_turn_requests",
                    "max_turn_requests": "max_turn_requests",
                    "refusal": "refusal",
                }.get(raw_reason)
                if stop_reason is None:
                    return _error(req_id, -32000, "Kernel returned an unsupported turn stop reason")
            return ACPResponse(id=req_id, result={"stopReason": stop_reason}).to_dict()
        except Exception:
            logger.exception("ACP v1 turn execution failed")
            return _error(req_id, -32000, "Turn execution failed")
        finally:
            if self._active_turns.get(session_id) is task:
                self._active_turns.pop(session_id, None)
            self._v1_cancel_requested.discard(session_id)
            self._v1_turn_barriers.pop(session_id, None)

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

    async def _write_v1_update(self, session_id: str, update: dict[str, Any]) -> None:
        params = self._safe_payload({"sessionId": session_id, "update": update})
        if params.get("sessionId") != session_id:
            raise ConnectionError("ACP v1 session identity cannot be safely represented")
        safe_update = params.get("update")
        if not isinstance(safe_update, dict):
            raise ConnectionError("ACP v1 update cannot be represented")
        for field in ("toolCallId",):
            if field in update and safe_update.get(field) != update[field]:
                raise ConnectionError("ACP v1 tool identity cannot be safely represented")
        await self._write(ACPNotification(method="session/update", params=params).to_dict())

    def _settle_v1_terminal(self, event: Any, data: dict[str, Any]) -> None:
        terminal_kinds = {
            "turn.completed": "turn.completed",
            "turn.cancelled": "turn.cancelled",
            "turn.error": "turn.error",
        }
        kind = terminal_kinds.get(event.type)
        if kind is None:
            return
        expected_record = kind
        record = getattr(event, "record", None)
        if not isinstance(record, dict) or record.get("kind") != expected_record:
            raise ConnectionError("ACP v1 terminal event has no matching durable record")
        barrier_entry = self._v1_turn_barriers.get(event.session_id)
        if barrier_entry is None:
            return
        barrier, turn_id = barrier_entry
        if not isinstance(event.turn_id, str) or not event.turn_id or turn_id != event.turn_id:
            return
        for rpc_id, pending in tuple(self._v1_pending_permissions.items()):
            if pending.session_id == event.session_id and pending.turn_id == event.turn_id:
                self._v1_pending_permissions.pop(rpc_id, None)
                if not pending.future.done():
                    pending.future.set_result((False, "ACP turn ended before permission was resolved"))
        for key, call_turn_id in tuple(self._v1_tool_calls.items()):
            if key[0] == event.session_id and call_turn_id == event.turn_id:
                self._v1_tool_calls.pop(key, None)
        if not barrier.done():
            barrier.set_result(kind)

    def _fail_v1_turn_barriers(self, error: BaseException) -> None:
        for barrier, _ in self._v1_turn_barriers.values():
            if not barrier.done():
                barrier.set_exception(error)

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
                if self._dialect == "v1":
                    if event.type == "turn.started":
                        entry = self._v1_turn_barriers.get(event.session_id)
                        if entry is not None and isinstance(event.turn_id, str) and event.turn_id:
                            self._v1_turn_barriers[event.session_id] = (entry[0], event.turn_id)
                            if event.session_id in self._v1_cancel_requested:
                                accepted = await self.commands.cancel_turn(session_id=event.session_id)
                                if accepted:
                                    self._v1_cancel_requested.discard(event.session_id)
                        continue
                    if event.type in {"turn.completed", "turn.cancelled", "turn.error"}:
                        self._settle_v1_terminal(event, data)
                        continue
                    if event.type == "approval.required":
                        if self._connected and not self._closing:
                            self._schedule_v1_approval(event, data)
                        continue
                    if event.type == "model.completed":
                        record = getattr(event, "record", None)
                        if not isinstance(record, dict) or record.get("kind") != "assistant.message":
                            raise ConnectionError("ACP v1 model completion has no assistant record")
                        continue
                    if event.type == "model.text.delta":
                        text = data.get("text")
                        if not isinstance(text, str):
                            raise ConnectionError("ACP v1 text delta cannot be represented")
                        barrier_entry = self._v1_turn_barriers.get(event.session_id)
                        if barrier_entry is not None and event.turn_id == barrier_entry[1]:
                            await self._write_v1_update(event.session_id, {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": text},
                            })
                        continue
                    if event.type == "model.thinking.delta":
                        text = data.get("text")
                        if not isinstance(text, str):
                            raise ConnectionError("ACP v1 thinking delta cannot be represented")
                        barrier_entry = self._v1_turn_barriers.get(event.session_id)
                        if barrier_entry is not None and event.turn_id == barrier_entry[1]:
                            await self._write_v1_update(event.session_id, {
                                "sessionUpdate": "agent_thought_chunk",
                                "content": {"type": "text", "text": text},
                            })
                        continue
                    if event.type == "tool.call":
                        call_id, name, arguments = data.get("call_id"), data.get("name"), data.get("arguments")
                        if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not isinstance(arguments, dict):
                            raise ConnectionError("ACP v1 tool call cannot be represented")
                        key = (event.session_id, call_id)
                        if key in self._v1_tool_calls:
                            raise ConnectionError("ACP v1 tool call identifier was reused")
                        barrier_entry = self._v1_turn_barriers.get(event.session_id)
                        if (
                            barrier_entry is None
                            or not isinstance(barrier_entry[1], str)
                            or (event.turn_id is not None and event.turn_id != barrier_entry[1])
                        ):
                            raise ConnectionError("ACP v1 tool call is outside its active turn")
                        self._v1_tool_calls[key] = barrier_entry[1]
                        await self._write_v1_update(event.session_id, {
                            "sessionUpdate": "tool_call",
                            "toolCallId": call_id,
                            "title": name,
                            "kind": "read" if name == "view_file" else "other",
                            # `tool.call` precedes policy resolution and any possible
                            # human approval, so it is truthful only as a pending start.
                            "status": "pending",
                            "rawInput": arguments,
                        })
                        continue
                    if event.type == "tool.result":
                        call_id = data.get("call_id")
                        if not isinstance(call_id, str) or not call_id:
                            raise ConnectionError("ACP v1 tool result has no call identifier")
                        key = (event.session_id, call_id)
                        call_turn_id = self._v1_tool_calls.pop(key, None)
                        if call_turn_id is None:
                            continue
                        barrier_entry = self._v1_turn_barriers.get(event.session_id)
                        if (
                            barrier_entry is None
                            or barrier_entry[1] != call_turn_id
                            or (event.turn_id is not None and event.turn_id != call_turn_id)
                        ):
                            raise ConnectionError("ACP v1 tool result turn does not match its call")
                        preview = data.get("preview")
                        if not isinstance(preview, str):
                            raise ConnectionError("ACP v1 tool result cannot be represented")
                        await self._write_v1_update(event.session_id, {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": call_id,
                            "status": "failed" if data.get("is_error") is True else "completed",
                            "content": [{
                                "type": "content",
                                "content": {"type": "text", "text": preview},
                            }],
                        })
                        continue
                    # Terminal, filesystem and elicitation RPCs remain unavailable.
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
            self._fail_v1_turn_barriers(ConnectionError("ACP update forwarding failed"))
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
        if request.get("method") == "session/prompt" and request.get("id") is not None and self._dialect == "v1":
            session_id, prompt, error = self._v1_prompt_args(request.get("id"), request.get("params", {}))
            if error is not None:
                return error, None
            assert session_id is not None and prompt is not None
            return None, self._schedule_v1_prompt(request.get("id"), session_id, prompt)
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
        self._closing = False
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
                forwarders = {
                    task for task in (self._bus_task, *self._question_tasks.values()) if task is not None
                } | set(self._v1_permission_tasks)
                done, _ = await asyncio.wait(
                    {reader_task, *turns.keys(), *forwarders},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if writer.is_closing():
                    raise ConnectionError("ACP output channel is closed")
                for task in forwarders:
                    if task in done:
                        is_permission_task = task in self._v1_permission_tasks
                        if is_permission_task:
                            self._v1_permission_tasks.discard(task)
                        if task.cancelled():
                            raise ConnectionError("ACP notification forwarding was cancelled")
                        error = task.exception()
                        if error is not None:
                            raise error
                        if not is_permission_task:
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
                    and request.get("method") in {"prompt", "session/prompt"}
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
            self._closing = True
            self._revoke_v1_approval_authority()
            self._abort_output(writer)
            raise
        finally:
            self._closing = True
            self._revoke_v1_approval_authority()
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
            self._cancel_v1_permissions(None, "ACP connection closed")
            owned_tasks = set(turns) | set(self._v1_permission_tasks)
            if owned_tasks:
                try:
                    async with asyncio.timeout(2.0):
                        await asyncio.gather(*owned_tasks, return_exceptions=True)
                        if not writer.is_closing():
                            for task in turns:
                                try:
                                    response = task.result()
                                except Exception:
                                    response = None
                                if response is not None:
                                    await self._write(response)
                except TimeoutError:
                    for task in owned_tasks:
                        task.cancel()
                    await asyncio.gather(*owned_tasks, return_exceptions=True)
                except Exception:
                    logger.exception("ACP response flush failed during EOF cleanup")
            self._v1_permission_tasks.clear()
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
            self._v1_tool_calls.clear()
            self._v1_cancel_requested.clear()
            self._v1_pending_permissions.clear()
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

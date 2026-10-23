"""
PolicyGate: the unified choke point for tool execution, sandboxing,
policy evaluation, approvals, timeouts, and output sanitization.
Fulfills KRN-06 and absorbs TOOL-01, TOOL-02, TOOL-05, and D9.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Literal

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.context import HookContextImpl, PolicyContextImpl, append_and_publish_record
from harness.kernel.interactions import InteractionBroker
from harness.kernel.jail import PathOutsideWorkspace, WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.sanitizer import sanitize_output
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore, validate_schema_value
from harness.sdk.context import PolicyContext, ToolContext
from harness.sdk.contracts import Executor, Policy, Process
from harness.sdk.errors import CapabilityDenied, SettingsValidationError
from harness.sdk.events import (
    Event,
    EventPayload,
    ToolCallResult,
    ToolCallStarted,
    ToolDecision,
    ToolOutputDelta,
    UiCard,
)
from harness.sdk.types import (
    RISK_ORDER,
    Action,
    Capability,
    Decision,
    HookEvent,
    RiskClass,
    TextBlock,
    ToolCall,
    ToolResult,
    ToolSpec,
    TranscriptRecord,
)

logger = logging.getLogger(__name__)


def _risk_rank(r: RiskClass | str | None) -> int:
    """Returns numerical rank of risk class according to RISK_ORDER."""
    if r is None:
        return -1
    try:
        rc = RiskClass(r) if isinstance(r, str) else r
        return RISK_ORDER.index(rc)
    except (ValueError, IndexError):
        return -1


class WorkspaceJailPolicy(Policy):
    """Normalises workspace path arguments and enforces workspace containment."""

    name = "workspace_jail"
    order = 10  # Evaluated first in policy chain

    def __init__(self, jail: WorkspaceJail):
        self.jail = jail

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision:
        props = (
            spec.input_schema.get("properties", {})
            if isinstance(spec.input_schema, dict)
            else {}
        )
        replacements: dict[str, Any] = {}
        is_write = spec.risk_class in (RiskClass.WRITE, RiskClass.DESTRUCTIVE)

        for arg_name, arg_val in call.arguments.items():
            prop_def = props.get(arg_name, {})
            # Match explicit format: workspace-path or common path field naming
            is_path_field = prop_def.get("format") == "workspace-path" or arg_name.lower() in (
                "targetfile",
                "cwd",
                "file_path",
                "filepath",
                "path",
            )
            if isinstance(arg_val, str) and is_path_field:
                try:
                    resolved = self.jail.resolve(arg_val, for_write=is_write)
                    replacements[arg_name] = str(resolved)
                except PathOutsideWorkspace as err:
                    return Decision(
                        action=Action.DENY,
                        reason=f"Path outside workspace: {err}",
                        risk=spec.risk_class,
                        policy=self.name,
                    )

        return Decision(
            action=Action.ALLOW,
            arguments=replacements if replacements else None,
            policy=self.name,
        )


class PermissionModesPolicy(Policy):
    """Fallback copy of the plugin policy for kernels booted without the policy plugin.

    The built-in plugin is preferred when registered; this fallback keeps small embedders and tests using the
    same fail-closed sandbox rules. Keep the two policy implementations in lockstep (the flow test pins the
    important decisions).
    """

    name = "permission_modes"
    order = 900

    def __init__(self, settings: SettingsStore, sandbox: Any = None):
        self.settings = settings
        self.sandbox = sandbox

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision:
        mode = self.settings.get("kernel.security.permission_mode", "ask")
        destructive_blocking = self.settings.get("kernel.security.destructive_blocking", True)
        sandbox_mode = self.settings.get("kernel.sandbox.mode", "off")
        exec_in_sandbox = self.settings.get("kernel.security.exec_in_sandbox", "ask")
        sandbox_status = self.sandbox.status() if self.sandbox is not None else {
            "mode": sandbox_mode,
            "backend": "none",
            "available": False,
            "enforcement": "none",
            "reason": "No sandbox service is registered.",
            "network": False,
        }
        uses_executor = spec.risk_class == RiskClass.EXEC or "exec" in spec.capabilities
        metadata = {"sandbox": sandbox_status} if uses_executor else {}
        risk = spec.risk_class
        capability_bypass = Capability.APPROVAL_BYPASS in getattr(ctx, "capabilities", frozenset())

        if mode == "plan" and not capability_bypass and risk in (RiskClass.WRITE, RiskClass.EXEC, RiskClass.DESTRUCTIVE):
            return Decision(action=Action.DENY, reason=f"Execution of '{call.name}' denied in plan mode.", risk=risk, policy=self.name, metadata=metadata)

        # A principal with approval:bypass can skip interactive approval, never a required OS boundary.
        if uses_executor and sandbox_mode == "required" and not sandbox_status.get("available"):
            return Decision(
                action=Action.DENY,
                reason=f"sandbox unavailable on this host: {sandbox_status.get('reason') or 'no OS sandbox backend is available'}",
                risk=risk,
                policy=self.name,
                metadata=metadata,
            )
        if sandbox_mode not in ("required", "preferred", "off") and uses_executor:
            return Decision(action=Action.DENY, reason=f"Invalid sandbox mode {sandbox_mode!r}; refusing executor call.", risk=risk, policy=self.name, metadata=metadata)

        if mode == "bypass" or capability_bypass:
            return Decision(action=Action.ALLOW, policy=self.name, metadata=metadata)
        if risk == RiskClass.DESTRUCTIVE and destructive_blocking:
            return Decision(action=Action.DENY, reason=f"Destructive action '{call.name}' blocked by security policy.", risk=risk, policy=self.name, metadata=metadata)

        if uses_executor and sandbox_mode == "preferred" and not sandbox_status.get("available"):
            if mode != "bypass" and not capability_bypass:
                detail = sandbox_status.get("reason") or "no OS sandbox backend is available"
                return Decision(action=Action.ASK, reason=f"Sandbox unavailable; approve unconfined execution for '{call.name}': {detail}", risk=risk, policy=self.name, metadata=metadata)
        if uses_executor and mode == "ask" and sandbox_mode != "off" and sandbox_status.get("available") is True and exec_in_sandbox == "allow":
            return Decision(action=Action.ALLOW, policy=self.name, metadata=metadata)
        if mode == "ask" and risk in (RiskClass.WRITE, RiskClass.EXEC, RiskClass.DESTRUCTIVE):
            return Decision(action=Action.ASK, reason=f"Approval required for '{call.name}' ({risk.value}).", risk=risk, policy=self.name, metadata=metadata)
        return Decision(action=Action.ALLOW, policy=self.name, metadata=metadata)

class ToolContextImpl(ToolContext):
    """ToolContext provided to tool handlers during execution."""

    def __init__(
        self,
        call_id: str,
        workspace: Path,
        data_dir: Path,
        capabilities: frozenset[Capability],
        executor: Executor | None,
        bus: EventBus | None = None,
        session_id: str = "",
        turn_id: str | None = None,
        turn_state: dict[str, Any] | None = None,
        log: Any = None,
        plugin_name: str = "",
        inbox: Any = None,
        job_service: Any = None,
        interactions: InteractionBroker | None = None,
        turn_cancel_event: asyncio.Event | None = None,
    ):
        # WS-03 cooperative timeout state, filled in by the gate around the handler call. This is deliberately
        # separate from the turn-level cancellation signal: timing out one tool must not cancel the whole turn.
        self.deadline: float | None = None
        self.cancel_event = asyncio.Event()
        self.cancel_reason: str | None = None
        self._turn_cancel_event = turn_cancel_event
        self._output_buffer: list[str] = []
        self._live_procs: list[Any] = []
        self._termination_errors: list[str] = []
        self._termination_tasks: set[asyncio.Task[int]] = set()
        self.call_id = call_id
        self.session_id = session_id
        self.turn_id = turn_id
        self.turn_state = turn_state if turn_state is not None else {}
        self.workspace = workspace
        self.data_dir = data_dir
        self.capabilities = capabilities
        self._executor = executor
        self._bus = bus
        self._session_id = session_id
        self._turn_id = turn_id
        self._log = log
        self._plugin_name = plugin_name
        self._inbox = inbox
        self._job_service = job_service
        self.jobs = (
            job_service.for_session(session_id, turn_id)
            if job_service is not None and session_id
            else None
        )
        self._interactions = interactions

    def remaining_time(self) -> float:
        """Seconds until the gate's handler deadline, or infinity outside a gated call."""
        if self.deadline is None:
            return float("inf")
        try:
            return max(0.0, self.deadline - asyncio.get_running_loop().time())
        except RuntimeError:
            return 0.0

    async def ask_user(
        self,
        questions: list[dict[str, Any]],
        timeout_s: float | None = None,
    ) -> dict[str, Any] | None:
        """Wait for the current interactive client to answer a structured question."""
        if self._interactions is None or self.remaining_time() <= 0:
            return None
        remaining = self.remaining_time()
        requested_timeout = 1800.0 if timeout_s is None else max(0.0, float(timeout_s))
        effective_timeout = min(remaining, requested_timeout)
        return await self._interactions.ask(
            "question",
            {"questions": questions, "call_id": self.call_id},
            timeout_s=effective_timeout,
            session_id=self._session_id,
            turn_id=self._turn_id,
            cancel_event=self._turn_cancel_event,
        )

    @property
    def executor(self) -> Executor:
        if Capability.EXEC not in self.capabilities:
            raise CapabilityDenied(
                capability="exec",
                plugin=self._plugin_name,
                message=f"Plugin '{self._plugin_name}' lacks Capability.EXEC to access executor",
            )
        if self._executor is None:
            raise RuntimeError("No executor configured in kernel.")
        return self._executor

    def track_process(self, proc: Any) -> None:
        """Registers a child process so the gate can stop it if the call runs out of time.

        A tool that spawns something (the shell executor does) and is then cancelled would otherwise
        leave that process running with the pipe still held open — the classic way a timed-out
        `run_command` leaks a `sleep 30` past the turn.
        """
        self._live_procs.append(proc)

    def drain_output_buffer(self, max_chars: int = 65536) -> str:
        """Everything streamed through ``emit_output_delta`` so far (for partial results)."""
        text = "".join(self._output_buffer)
        self._output_buffer.clear()
        return text[-max_chars:] if len(text) > max_chars else text

    async def terminate_tracked(self) -> int:
        """Best-effort kill of every still-running tracked process. Returns how many were alive."""
        processes, self._live_procs = self._live_procs, []
        if processes:
            task = asyncio.create_task(self._terminate_snapshot(processes))
            self._termination_tasks.add(task)
            task.add_done_callback(self._termination_tasks.discard)
        pending = tuple(self._termination_tasks)
        if not pending:
            return 0
        # Ownership covers the WHOLE snapshot, not just whichever kill was
        # active when the requesting task was cancelled.
        return sum(await asyncio.shield(asyncio.gather(*pending)))

    async def _terminate_snapshot(self, processes: list[Any]) -> int:
        alive = 0
        for proc in processes:
            try:
                if getattr(proc, "returncode", None) is None:
                    alive += 1
                kill = getattr(proc, "kill", None)
                if kill is not None:
                    try:
                        outcome = kill(grace_s=0.5)
                    except TypeError:
                        outcome = kill()  # Raw asyncio Process used by embedders/tests.
                    if hasattr(outcome, "__await__"):
                        await outcome
                wait = getattr(proc, "wait", None)
                if callable(wait):
                    await asyncio.wait_for(wait(), timeout=0.5)
            except Exception as err:  # noqa: BLE001 — cleanup must not mask the timeout
                self._termination_errors.append(type(err).__name__)
                logger.debug("terminate_tracked: %s", err)
        return alive

    def emit_output_delta(self, stream: Literal["stdout", "stderr"], text: str) -> None:
        # Kept for the timeout path: whatever a tool streamed is the partial output it can show.
        if text:
            self._output_buffer.append(text)
        if self._bus:
            evt = Event(
                type="tool.output.delta",
                session_id=self._session_id,
                turn_id=self._turn_id,
                data=ToolOutputDelta(
                    call_id=self.call_id,
                    stream=stream,
                    text=text,
                ),
            )
            self._bus.publish(evt)

    async def emit_record(
        self,
        payload: EventPayload,
        *,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Persist a durable plugin fact before notifying frontends."""
        return await append_and_publish_record(
            log=self._log,
            bus=self._bus,
            session_id=self._session_id,
            turn_id=self._turn_id,
            payload=payload,
            content=content,
            metadata=metadata,
        )

    def background(self, proc: Process, label: str = "") -> str:
        """Registers a process with the durable jobs service when available, else the legacy inbox."""
        if self.jobs is not None and self._inbox is self._job_service:
            return self.jobs.register_process(proc, label=label)
        import uuid

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        if self._inbox is not None:
            try:
                self._inbox.register_task(task_id, proc, label=label, session_id=self._session_id)
            except TypeError:
                self._inbox.register_task(task_id, proc, label=label)
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Gets status dictionary for a registered task or job."""
        if self.jobs is not None:
            return self.jobs.get_task(task_id)
        if self._inbox is not None:
            try:
                return self._inbox.get_status(task_id, session_id=self._session_id)
            except TypeError:
                return self._inbox.get_status(task_id)
        return None

    def list_tasks(self) -> list[dict[str, Any]]:
        """Lists tasks/jobs in this session."""
        if self.jobs is not None:
            return self.jobs.list_tasks()
        if self._inbox is not None:
            try:
                return self._inbox.list_tasks(session_id=self._session_id)
            except TypeError:
                return self._inbox.list_tasks()
        return []

    async def kill_task(self, task_id: str) -> bool:
        """Terminates a registered task or job."""
        if self.jobs is not None:
            return await self.jobs.kill_task(task_id)
        if self._inbox is not None:
            try:
                return await self._inbox.kill(task_id, session_id=self._session_id)
            except TypeError:
                return await self._inbox.kill(task_id)
        return False

    async def send_task_input(self, task_id: str, text: str) -> bool:
        """Sends input text to a running background task or job."""
        if self.jobs is not None:
            return await self.jobs.send_task_input(task_id, text)
        if self._inbox is not None:
            try:
                return await self._inbox.send_input(task_id, text, session_id=self._session_id)
            except TypeError:
                return await self._inbox.send_input(task_id, text)
        return False

    async def wait_task(self, task_id: str, timeout_s: float | None = None) -> Any:
        """Waits for a background task/job to complete and returns its exit code."""
        if self.jobs is not None:
            return await self.jobs.wait_task(task_id, timeout_s=timeout_s)
        if self._inbox is not None:
            try:
                return await self._inbox.wait_task(task_id, timeout_s=timeout_s, session_id=self._session_id)
            except TypeError:
                return await self._inbox.wait_task(task_id, timeout_s=timeout_s)
        return None


class PolicyGate:
    """
    Unified gatekeeper executing steps 1 through 9 for every tool invocation.
    Enforces that no handler ever executes without all gate invariants held.
    """

    def __init__(
        self,
        registry: PluginRegistry,
        jail: WorkspaceJail,
        settings: SettingsStore,
        secrets: Secrets,
        approvals: ApprovalBroker,
        bus: EventBus | None = None,
        inbox: Any = None,
    ):
        self.registry = registry
        self.jail = jail
        self.settings = settings
        self.secrets = secrets
        self.approvals = approvals
        self.bus = bus
        self.inbox = inbox
        get_service = getattr(registry, "get_service", None)
        self.jobs = get_service("jobs") if callable(get_service) else None

        # Built-in kernel policies
        self.jail_policy = WorkspaceJailPolicy(jail)
        # The policy plugin is the source when installed; this gate-local version is only the fallback for an
        # embedder that booted without it. It used to be appended *in addition* to the registered policy, which
        # duplicated ASK reasons and meant two independent implementations could disagree.
        registered_modes = next(
            (p for p, _plugin in registry.policies if getattr(p, "name", None) == "permission_modes"),
            None,
        )
        self.permission_modes_policy = registered_modes or PermissionModesPolicy(
            settings, sandbox=registry.sandbox
        )

    @property
    def specs(self) -> dict[str, ToolSpec]:
        """Returns map of tool name to ToolSpec for all registered tools."""
        return {name: entry[0] for name, entry in self.registry.tools.items()}

    def register_tool(
        self, spec: ToolSpec, handler: Any, plugin_name: str = "builtin"
    ) -> None:
        """Helper to register a tool with the underlying registry."""
        self.registry._register_tool(spec, handler, plugin_name)

    async def _record(
        self, log: Any, kind: str, content: str, metadata: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Appends a policy fact to the session log and returns it for the event to carry.

        Best-effort by design: the decision has usually already been taken (a denial, an approval the user
        answered), and losing a summary line must not turn a safe call into an unsafe one or vice versa.
        The warning is the signal — and the client's fallback for a missing `record` is to read the log.
        """
        if log is None or not kind:
            return None
        try:
            from harness.kernel.transcript import record_at_step

            step = await log.append(TranscriptRecord(kind=kind, role="system", content=content, metadata=metadata))
            return record_at_step(log.records, step)
        except Exception as e:
            logger.warning(f"Could not record {kind} in the session log: {e}")
            return None

    async def execute(
        self,
        call: ToolCall,
        options: dict[str, Any] | None = None,
        session_id: str = "",
        turn_id: str | None = None,
        principal_capabilities: frozenset[Capability] = frozenset(),
        turn_state: dict[str, Any] | None = None,
        log: Any = None,
        cancel_event: asyncio.Event | None = None,
    ) -> ToolResult:
        """Executes a single tool call through the complete 9-step PolicyGate pipeline.

        `log` is the session's `TranscriptLog` (WS-04b). The gate owns the *policy* facts about a call —
        that it was denied, that it needed an approval, which card it produced — and until now those only
        existed as live events, so a reloaded session could not explain itself. Passing the log makes the
        gate append each such record and then publish the event that carries it; without one (unit tests,
        direct gate use) the pipeline behaves exactly as before, minus the history.

        `turn_state` is the per-turn scratch dict the runner owns (WS-03): hooks share it through
        `hook_ctx` instead of module globals, so two concurrent sessions cannot see each other's
        repeat counters and a restart never strands half a turn's state.
        """
        start_time = time.time()
        options = options or {}
        call_id = getattr(call, "id", getattr(call, "call_id", "call_unknown"))

        # ---------------------------------------------------------
        # Step 1: Resolve tool spec and validate input arguments
        # ---------------------------------------------------------
        if call.name not in self.registry.tools:
            err_msg = f"unknown tool '{call.name}'"
            res = ToolResult.error(err_msg)
            if self.bus:
                self.bus.publish(
                    Event(
                        type="tool.result",
                        session_id=session_id,
                        data=ToolCallResult(
                            call_id=call_id,
                            name=call.name,
                            is_error=True,
                            duration_ms=0.0,
                            preview=err_msg,
                            metadata={},
                        ),
                    )
                )
            return res

        spec, handler, plugin_name = self.registry.tools[call.name]
        risk_val = spec.risk_class.value if hasattr(spec.risk_class, "value") else str(spec.risk_class)

        # Validate arguments against JSON Schema
        try:
            validate_schema_value(call.arguments, spec.input_schema, path=call.name)
        except SettingsValidationError as err:
            err_msg = f"Invalid arguments for tool '{spec.name}': {err}"
            res = ToolResult.error(err_msg)
            if self.bus:
                self.bus.publish(
                    Event(
                        type="tool.result",
                        session_id=session_id,
                        data=ToolCallResult(
                            call_id=call_id,
                            name=spec.name,
                            is_error=True,
                            duration_ms=0.0,
                            preview=err_msg[:2048],
                            metadata={},
                        ),
                    )
                )
            return res

        # ---------------------------------------------------------
        # Step 2: PRE_TOOL_USE hooks & initial event
        # ---------------------------------------------------------
        current_args = dict(call.arguments)
        if self.bus:
            self.bus.publish(
                Event(
                    type="tool.call",
                    session_id=session_id,
                    data=ToolCallStarted(
                        call_id=call_id,
                        name=spec.name,
                        arguments=current_args,
                        risk=risk_val,
                        plugin=plugin_name,
                    ),
                )
            )

        hook_ctx = HookContextImpl(
            session_id=session_id,
            workspace=self.registry.workspace,
            turn_id=turn_id,
            turn_state=turn_state if turn_state is not None else {},
            bus=self.bus,
            log=log,
            settings=self.settings,
        )
        for hook, _h_plugin in self.registry.hooks:
            is_pre = getattr(hook, "event", None) == HookEvent.PRE_TOOL_USE or (
                hasattr(hook, "events") and HookEvent.PRE_TOOL_USE in hook.events
            )
            if is_pre:
                try:
                    async with asyncio.timeout(5.0):
                        if hasattr(hook, "execute"):
                            outcome = await hook.execute(call, spec, hook_ctx)
                        else:
                            outcome = await hook.on(
                                HookEvent.PRE_TOOL_USE,
                                {"call": call, "spec": spec},
                                hook_ctx,
                            )
                    if outcome.action == "block":
                        reason = outcome.reason or "Blocked by hook"
                        err_msg = f"blocked by hook {hook.name}: {reason}"
                        dur_ms = (time.time() - start_time) * 1000
                        decision_rec = await self._record(
                            log,
                            "tool.decision",
                            f"Tool `{call.name}` denied by hook `{hook.name}`: {reason}",
                            {
                                "call_id": call_id,
                                "plugin": plugin_name,
                                "action": "deny",
                                "policy": f"hook:{hook.name}",
                                "risk": risk_val,
                                "reason": reason,
                            },
                        )
                        if self.bus:
                            self.bus.publish(
                                Event(
                                    type="tool.decision",
                                    session_id=session_id,
                                    data=ToolDecision(
                                        call_id=call_id,
                                        action="deny",
                                        plugin=plugin_name,
                                        policy=f"hook:{hook.name}",
                                        risk=risk_val,
                                        reason=reason,
                                    ),
                                    record=decision_rec,
                                )
                            )
                            self.bus.publish(
                                Event(
                                    type="tool.result",
                                    session_id=session_id,
                                    data=ToolCallResult(
                                        call_id=call_id,
                                        name=spec.name,
                                        is_error=True,
                                        duration_ms=dur_ms,
                                        preview=err_msg[:2048],
                                        metadata={"blocked_by": hook.name},
                                    ),
                                )
                            )
                        return ToolResult.error(err_msg)
                    if outcome.action == "mutate" and outcome.arguments is not None:
                        current_args.update(outcome.arguments)
                except Exception as err:
                    logger.warning(f"PRE_TOOL_USE hook '{hook.name}' failed: {err}")

        # ---------------------------------------------------------
        # Step 3: Policy chain evaluation & combination
        # ---------------------------------------------------------
        # Gather all policies: jail (order 10) -> registered policies -> modes (order 900)
        policies: list[Policy] = [self.jail_policy]
        for p, _ in self.registry.policies:
            # The gate chooses one `permission_modes` implementation above (registered plugin or fallback), then
            # appends it once at the end. The previous chain appended both a registered policy and its fallback.
            if getattr(p, "name", None) != "permission_modes":
                policies.append(p)
        policies.append(self.permission_modes_policy)
        policies.sort(key=lambda p: getattr(p, "order", 500))

        policy_ctx = PolicyContextImpl(
            workspace=self.registry.workspace,
            capabilities=principal_capabilities,
        )

        effective_risk = spec.risk_class
        decisions: list[Decision] = []
        eval_call = ToolCall(
            id=call_id, name=spec.name, arguments=dict(current_args)
        )

        for policy in policies:
            try:
                dec = await policy.evaluate(eval_call, spec, policy_ctx)
            except Exception as err:
                logger.warning(f"Policy '{policy.name}' raised error: {err}")
                dec = Decision(
                    action=Action.DENY,
                    reason=f"Policy error in {policy.name}: {err}",
                    policy=policy.name,
                )

            decisions.append(dec)
            if dec.arguments:
                current_args.update(dec.arguments)
                eval_call = ToolCall(
                    id=call_id, name=spec.name, arguments=dict(current_args)
                )
            if dec.risk and _risk_rank(dec.risk) > _risk_rank(effective_risk):
                effective_risk = dec.risk

        decision_metadata = next((d.metadata for d in decisions if d.metadata), {})
        effective_risk_val = effective_risk.value if hasattr(effective_risk, "value") else str(effective_risk)

        # Policy combination logic:
        # Any DENY wins -> deny
        # Else any ASK -> ask
        # Else -> allow
        denials = [d for d in decisions if d.action == Action.DENY]
        asks = [d for d in decisions if d.action == Action.ASK]

        if denials:
            first_deny = denials[0]
            err_msg = f"Action denied: {first_deny.reason}"
            dur_ms = (time.time() - start_time) * 1000
            decision_rec = await self._record(
                log,
                "tool.decision",
                f"Tool `{call.name}` denied by `{first_deny.policy or 'policy'}`: {first_deny.reason}",
                {
                    "call_id": call_id,
                    "plugin": plugin_name,
                    "action": "deny",
                    "policy": first_deny.policy or "policy",
                    "risk": effective_risk_val,
                    "reason": first_deny.reason,
                    **({"metadata": decision_metadata} if decision_metadata else {}),
                },
            )
            if self.bus:
                self.bus.publish(
                    Event(
                        type="tool.decision",
                        session_id=session_id,
                        data=ToolDecision(
                            call_id=call_id,
                            action="deny",
                            plugin=plugin_name,
                            policy=first_deny.policy or "policy",
                            risk=effective_risk_val,
                            reason=first_deny.reason,
                            metadata=decision_metadata or None,
                        ),
                        record=decision_rec,
                    )
                )
                self.bus.publish(
                    Event(
                        type="tool.result",
                        session_id=session_id,
                        data=ToolCallResult(
                            call_id=call_id,
                            name=spec.name,
                            is_error=True,
                            duration_ms=dur_ms,
                            preview=err_msg[:2048],
                            metadata={"policy": first_deny.policy, **decision_metadata},
                        ),
                    )
                )
            return ToolResult.error(err_msg)

        # ---------------------------------------------------------
        # Step 4 & 5: Approvals (when ASK is returned)
        # ---------------------------------------------------------
        approved_by_human = False
        if asks:
            reasons = "; ".join(a.reason for a in asks if a.reason)
            decision_rec = await self._record(
                log,
                "tool.decision",
                f"Tool `{call.name}` needs approval: {reasons or 'policy requires it'}",
                {
                    "call_id": call_id,
                    "plugin": plugin_name,
                    "action": "ask",
                    "policy": asks[0].policy or "policy",
                    "risk": effective_risk_val,
                    "reason": reasons,
                    **({"metadata": decision_metadata} if decision_metadata else {}),
                },
            )
            if self.bus:
                self.bus.publish(
                    Event(
                        type="tool.decision",
                        session_id=session_id,
                        data=ToolDecision(
                            call_id=call_id,
                            action="ask",
                            plugin=plugin_name,
                            policy=asks[0].policy or "policy",
                            risk=effective_risk_val,
                            reason=reasons,
                            metadata=decision_metadata or None,
                        ),
                        record=decision_rec,
                    )
                )

            auto_approve = options.get("auto_approve", False)
            timeout_s = self.settings.get("kernel.security.approval_timeout_s", 300)
            approved, appr_reason = await self.approvals.ask(
                kind="tool_execution",
                summary=f"Tool '{spec.name}' execution requires approval",
                details={
                    "call_id": call_id,
                    "name": spec.name,
                    "arguments": current_args,
                    "risk": effective_risk_val,
                },
                call_id=call_id,
                timeout_s=float(timeout_s),
                auto_approve=auto_approve,
                session_id=session_id,
                cancel_event=cancel_event,
            )

            if not approved:
                err_msg = f"Approval denied: {appr_reason}"
                dur_ms = (time.time() - start_time) * 1000
                if self.bus:
                    self.bus.publish(
                        Event(
                            type="tool.result",
                            session_id=session_id,
                            data=ToolCallResult(
                                call_id=call_id,
                                name=spec.name,
                                is_error=True,
                                duration_ms=dur_ms,
                                preview=err_msg[:2048],
                                metadata={"approval": "denied", **decision_metadata},
                            ),
                        )
                    )
                return ToolResult.error(err_msg)
            approved_by_human = True

        # Action is allowed. The record is written whether or not a subscriber is attached — the log is
        # not the bus's copy of itself.
        if not asks:
            allow_rec = await self._record(
                log,
                "tool.decision",
                f"Tool `{call.name}` allowed ({effective_risk_val}).",
                {
                    "call_id": call_id,
                    "plugin": plugin_name,
                    "action": "allow",
                    "policy": "policy_gate",
                    "risk": effective_risk_val,
                    "reason": "OK",
                    **({"metadata": decision_metadata} if decision_metadata else {}),
                },
            )
            if self.bus:
                self.bus.publish(
                    Event(
                        type="tool.decision",
                        session_id=session_id,
                        data=ToolDecision(
                            call_id=call_id,
                            action="allow",
                            plugin=plugin_name,
                            policy="policy_gate",
                            risk=effective_risk_val,
                            reason="OK",
                            metadata=decision_metadata or None,
                        ),
                        record=allow_rec,
                    )
                )

        # ---------------------------------------------------------
        # Step 6: Build capability-gated ToolContext
        # ---------------------------------------------------------
        plugin_info = self.registry.get_loaded(plugin_name)
        plugin_caps = (
            plugin_info.effective_capabilities if plugin_info else frozenset()
        )
        effective_caps = frozenset(spec.capabilities) & plugin_caps

        executor = None
        if self.registry.executors:
            if "default" in self.registry.executors:
                executor = self.registry.executors["default"][0]
            elif "local" in self.registry.executors:
                executor = self.registry.executors["local"][0]
            else:
                executor = next(iter(self.registry.executors.values()))[0]
        sandbox_service = getattr(self.registry, "sandbox", None)
        if executor is not None and sandbox_service is not None:
            bypass_mode = self.settings.get("kernel.security.permission_mode", "ask") == "bypass"
            executor = sandbox_service.wrap_executor(
                executor,
                allow_unconfined=(
                    approved_by_human
                    or bypass_mode
                    or Capability.APPROVAL_BYPASS in principal_capabilities
                ),
            )
        tool_ctx = ToolContextImpl(
            call_id=call_id,
            workspace=self.registry.workspace,
            data_dir=self.registry.data_dir / "tools" / spec.name,
            capabilities=effective_caps,
            executor=executor,
            bus=self.bus,
            session_id=session_id,
            turn_id=turn_id,
            turn_state=turn_state,
            log=log,
            plugin_name=plugin_name,
            inbox=self.inbox,
            job_service=self.jobs,
            interactions=self.approvals.interactions,
            turn_cancel_event=cancel_event,
        )

        # ---------------------------------------------------------
        # Step 7: Run tool handler under a cooperative timeout
        # ---------------------------------------------------------
        # `asyncio.timeout` alone would cancel the handler mid-await and throw away whatever it had
        # already streamed, and would leave any child process running. So: signal first
        # (`cancel_event`), allow `tool_grace_s` for a clean return, then cancel — and stop the
        # children either way. The result is the same text the model is told about (P1).
        grace_s = float(self.settings.get("kernel.limits.tool_grace_s", 5.0) or 0.0)
        tool_ctx.deadline = asyncio.get_running_loop().time() + float(spec.timeout_s)
        async def run_handler() -> ToolResult:
            # Run this check inside the scheduled task, not before create_task:
            # cancellation may arrive between approval and the task's first step.
            if cancel_event is not None and cancel_event.is_set():
                return ToolResult.error("Tool execution cancelled before handler start", cancelled=True)
            return await handler(current_args, tool_ctx)

        task = asyncio.create_task(run_handler())
        turn_cancel_task: asyncio.Task | None = None
        if cancel_event is not None:
            async def relay_turn_cancellation() -> None:
                await cancel_event.wait()
                tool_ctx.cancel_reason = "turn"
                tool_ctx.cancel_event.set()

            turn_cancel_task = asyncio.create_task(relay_turn_cancellation())
        timed_out = False
        try:
            waiters = {task}
            if turn_cancel_task is not None:
                waiters.add(turn_cancel_task)
            done, _ = await asyncio.wait(waiters, timeout=spec.timeout_s, return_when=asyncio.FIRST_COMPLETED)
            if cancel_event is not None and cancel_event.is_set():
                tool_ctx.cancel_reason = "turn"
                tool_ctx.cancel_event.set()
                await tool_ctx.terminate_tracked()
                try:
                    result = await asyncio.wait_for(asyncio.shield(task), timeout=min(grace_s, 0.5))
                except (TimeoutError, Exception):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    result = ToolResult.error("Tool stopped after turn cancellation")
                await tool_ctx.terminate_tracked()  # Include a child registered during startup/cleanup.
                partial = tool_ctx.drain_output_buffer()
                result = ToolResult(
                    content=([TextBlock(text=partial)] if partial else []) + list(result.content),
                    is_error=True,
                    metadata={**dict(result.metadata), "cancelled": True, "effects_may_remain": True},
                )
                if tool_ctx._termination_errors:
                    result.metadata.update(termination_confirmed=False, termination_errors=list(tool_ctx._termination_errors))
            elif task in done:
                result = task.result()
            else:
                raise TimeoutError
        except TimeoutError:
            timed_out = True
            tool_ctx.cancel_reason = "timeout"
            tool_ctx.cancel_event.set()
            timeout_note = f"Tool '{spec.name}' exceeded {spec.timeout_s:g}s"
            returned_in_grace = False
            try:
                # The handler may still return something useful inside the grace period.
                grace_waiters = {task}
                if turn_cancel_task is not None:
                    grace_waiters.add(turn_cancel_task)
                grace_done, _ = await asyncio.wait(grace_waiters, timeout=grace_s, return_when=asyncio.FIRST_COMPLETED)
                if cancel_event is not None and cancel_event.is_set():
                    tool_ctx.cancel_reason = "turn"
                    task.cancel()
                    await tool_ctx.terminate_tracked()
                    await asyncio.gather(task, return_exceptions=True)
                    result = ToolResult.error("Turn cancelled during tool timeout cleanup; partial effects may remain", cancelled=True)
                elif task in grace_done:
                    result = task.result()
                    returned_in_grace = True
                else:
                    raise TimeoutError
            except TimeoutError:
                task.cancel()
                # Await the cancellation so the handler's own cleanup runs before the turn moves on;
                # gathering without it would leave a pending task nobody owns.
                await asyncio.gather(task, return_exceptions=True)
                result = ToolResult.error(f"{timeout_note} and was cancelled.")
            except asyncio.CancelledError:
                tool_ctx.cancel_reason = "turn"
                tool_ctx.cancel_event.set()
                task.cancel()
                await tool_ctx.terminate_tracked()
                await asyncio.gather(task, return_exceptions=True)
                raise
            except Exception as err:  # noqa: BLE001 — the handler failing on the way out is not new info
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                result = ToolResult.error(f"{timeout_note} and was cancelled ({type(err).__name__}: {err})")
            await tool_ctx.terminate_tracked()
            # Whatever the tool managed to say is kept, but the result is always an error: an
            # incomplete answer must never look like a completed one to the model.
            partial = tool_ctx.drain_output_buffer()
            metadata = {**dict(result.metadata), "timed_out": True}
            if partial:
                metadata["partial_output"] = True
                head = f"{timeout_note}; partial output follows.\n{partial}"
            elif returned_in_grace:
                head = f"{timeout_note}; the tool returned what it had before stopping."
            else:
                head = ""
            content = ([TextBlock(text=head)] if head else []) + list(result.content)
            result = ToolResult(content=content, is_error=True, metadata=metadata)
        except asyncio.CancelledError:
            tool_ctx.cancel_reason = "turn"
            tool_ctx.cancel_event.set()
            await tool_ctx.terminate_tracked()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await tool_ctx.terminate_tracked()
            raise
        except Exception as err:
            logger.exception(f"Unhandled exception in tool '{spec.name}': {err}")
            result = ToolResult.error(f"{type(err).__name__}: {err}")
        finally:
            if turn_cancel_task is not None:
                turn_cancel_task.cancel()
                await asyncio.gather(turn_cancel_task, return_exceptions=True)
            tool_ctx.deadline = None
            if not timed_out:
                tool_ctx.drain_output_buffer()  # streamed output is already in the result; drop the copy

        # ---------------------------------------------------------
        # Step 8: POST_TOOL_USE hooks
        # ---------------------------------------------------------
        for hook, _h_plugin in self.registry.hooks:
            is_post = getattr(hook, "event", None) == HookEvent.POST_TOOL_USE or (
                hasattr(hook, "events") and HookEvent.POST_TOOL_USE in hook.events
            )
            if is_post:
                try:
                    async with asyncio.timeout(5.0):
                        if hasattr(hook, "execute"):
                            post_outcome = await hook.execute(call, result, hook_ctx)
                        else:
                            post_outcome = await hook.on(
                                HookEvent.POST_TOOL_USE,
                                {"call": call, "result": result},
                                hook_ctx,
                            )
                    if post_outcome.additional_context:
                        result = ToolResult(
                            content=list(result.content)
                            + [TextBlock(text=post_outcome.additional_context)],
                            is_error=result.is_error,
                            metadata=dict(result.metadata),
                        )
                    if post_outcome.action == "block":
                        result = ToolResult(
                            content=[
                                TextBlock(
                                    text=f"Blocked by post-tool hook: {post_outcome.reason}"
                                )
                            ],
                            is_error=True,
                            metadata=dict(result.metadata),
                        )
                except Exception as err:
                    logger.warning(f"POST_TOOL_USE hook '{hook.name}' failed: {err}")

        # ---------------------------------------------------------
        # Step 8b: TOOL_RESULT_FINALIZE hooks (WS-03) — size guards live here
        # ---------------------------------------------------------
        # Ordered, and allowed to replace the result (`action="mutate"` with `outcome.result`).
        # This is the last point at which a hook can decide what the model reads: what survives
        # here is what gets sanitised, truncated, recorded and rendered.
        finalize_hooks = [
            (hook, hp)
            for hook, hp in self.registry.hooks
            if getattr(hook, "event", None) == HookEvent.TOOL_RESULT_FINALIZE
            or (hasattr(hook, "events") and HookEvent.TOOL_RESULT_FINALIZE in hook.events)
        ]
        for hook, _hp in sorted(finalize_hooks, key=lambda item: getattr(item[0], "order", 500)):
            try:
                async with asyncio.timeout(5.0):
                    outcome = await hook.on(
                        HookEvent.TOOL_RESULT_FINALIZE,
                        {
                            "call": call,
                            "result": result,
                            "session_id": session_id,
                            "spec": spec,
                        },
                        hook_ctx,
                    )
                if outcome.action == "mutate" and outcome.result is not None:
                    result = outcome.result
            except Exception as err:
                logger.warning(f"TOOL_RESULT_FINALIZE hook '{hook.name}' failed: {err}")

        # ---------------------------------------------------------
        # Step 9: Secret sanitization and size truncation
        # ---------------------------------------------------------
        # Extract secret values from Secrets store for exact-match redaction
        secret_values: list[str] = []
        try:
            for _k, v in self.secrets._load().items():
                if isinstance(v, str) and len(v) >= 6:
                    secret_values.append(v)
        except Exception:
            pass

        sanitized_content = []
        full_content_str = ""
        max_bytes = self.settings.get("kernel.limits.tool_result_bytes", 65536)

        for block in result.content:
            if isinstance(block, TextBlock):
                full_content_str += block.text
                sanitized_text = sanitize_output(block.text, secret_values)
                # Check byte truncation
                encoded = sanitized_text.encode("utf-8")
                if len(encoded) > max_bytes:
                    truncated_text = (
                        encoded[:max_bytes].decode("utf-8", errors="ignore")
                        + "\n... [TRUNCATED DUE TO SIZE LIMIT]"
                    )
                    sanitized_content.append(TextBlock(text=truncated_text))
                    result.metadata["truncated"] = True
                    result.metadata["full_content"] = sanitized_text
                else:
                    sanitized_content.append(TextBlock(text=sanitized_text))
            else:
                sanitized_content.append(block)

        duration_ms = (time.time() - start_time) * 1000
        final_metadata = dict(result.metadata)
        final_metadata["duration_ms"] = duration_ms

        final_result = ToolResult(
            content=sanitized_content,
            is_error=result.is_error,
            metadata=final_metadata,
            cards=list(result.cards) if result.cards else [],
        )

        preview_text = ""
        for b in final_result.content:
            if isinstance(b, TextBlock):
                preview_text += b.text
        if len(preview_text) > 2048:
            preview_text = preview_text[:2048] + "..."

        # A card is a fact, not a frame. Record every card the tool produced first — a subscriber sees it
        # live, the log is what it said — then publish the live view carrying the stored record.
        card_rows: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for card in (result.cards or []):
            card_dict = card.to_dict() if hasattr(card, "to_dict") else card
            card_rows.append(
                (
                    card_dict,
                    await self._record(
                        log,
                        "ui.card",
                        str(card_dict.get("title") or card_dict.get("kind") or "card"),
                        {"card": card_dict, "call_id": call_id},
                    ),
                )
            )

        if self.bus:
            for card_dict, card_rec in card_rows:
                self.bus.publish(
                    Event(
                        type="ui.card",
                        session_id=session_id,
                        data=UiCard(card=card_dict),
                        record=card_rec,
                    )
                )
            self.bus.publish(
                Event(
                    type="tool.result",
                    session_id=session_id,
                    data=ToolCallResult(
                        call_id=call_id,
                        name=spec.name,
                        is_error=final_result.is_error,
                        duration_ms=duration_ms,
                        preview=preview_text,
                        metadata=final_metadata,
                    ),
                )
            )

        return final_result

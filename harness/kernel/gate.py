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
from harness.kernel.context import HookContextImpl, PolicyContextImpl
from harness.kernel.jail import PathOutsideWorkspace, WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.sanitizer import sanitize_output
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore, validate_schema_value
from harness.sdk.context import PolicyContext, ToolContext
from harness.sdk.contracts import Executor, Policy, Process
from harness.sdk.errors import CapabilityDenied, SettingsValidationError
from harness.sdk.events import Event, ToolCallResult, ToolCallStarted, ToolDecision, ToolOutputDelta, UiCard
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
    """Enforces plan, allow, ask, and bypass permission modes."""

    name = "permission_modes"
    order = 900  # Evaluated towards the end of policy chain

    def __init__(self, settings: SettingsStore):
        self.settings = settings

    async def evaluate(self, call: ToolCall, spec: ToolSpec, ctx: PolicyContext) -> Decision:
        # Check if session principal holds approval:bypass capability
        # Note: approval:bypass is NEVER grantable to plugins (clamped by registry)
        if Capability.APPROVAL_BYPASS in ctx.capabilities:
            return Decision(action=Action.ALLOW, policy=self.name)

        mode = self.settings.get("kernel.security.permission_mode", "ask")
        risk = spec.risk_class

        if mode == "plan":
            if risk != RiskClass.READ:
                return Decision(
                    action=Action.DENY,
                    reason=f"Permission mode 'plan' denies non-read action '{spec.name}' (risk: {risk.value})",
                    risk=risk,
                    policy=self.name,
                )
            return Decision(action=Action.ALLOW, policy=self.name)

        if mode == "allow":
            if risk == RiskClass.DESTRUCTIVE:
                return Decision(
                    action=Action.ASK,
                    reason=f"Destructive action '{spec.name}' requires approval in 'allow' mode",
                    risk=risk,
                    policy=self.name,
                )
            return Decision(action=Action.ALLOW, policy=self.name)

        if mode == "ask":
            if risk == RiskClass.READ:
                return Decision(action=Action.ALLOW, policy=self.name)

            if risk == RiskClass.DESTRUCTIVE:
                destructive_blocking = self.settings.get(
                    "kernel.security.destructive_blocking", True
                )
                if destructive_blocking:
                    return Decision(
                        action=Action.DENY,
                        reason=f"Destructive action '{spec.name}' blocked by kernel security policy",
                        risk=risk,
                        policy=self.name,
                    )

            return Decision(
                action=Action.ASK,
                reason=f"Action '{spec.name}' (risk: {risk.value}) requires user approval in 'ask' mode",
                risk=risk,
                policy=self.name,
            )

        return Decision(action=Action.ALLOW, policy=self.name)


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
        plugin_name: str = "",
        inbox: Any = None,
    ):
        # WS-03 cooperative timeout state, filled in by the gate around the handler call.
        self.deadline: float | None = None
        self.cancel_event = asyncio.Event()
        self._output_buffer: list[str] = []
        self._live_procs: list[Any] = []
        self.call_id = call_id
        self.workspace = workspace
        self.data_dir = data_dir
        self.capabilities = capabilities
        self._executor = executor
        self._bus = bus
        self._session_id = session_id
        self._plugin_name = plugin_name
        self._inbox = inbox

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
        alive = 0
        for proc in self._live_procs:
            try:
                if getattr(proc, "returncode", None) is None:
                    alive += 1
                kill = getattr(proc, "kill", None)
                if kill is not None:
                    outcome = kill()
                    if hasattr(outcome, "__await__"):
                        await outcome
            except Exception as err:  # noqa: BLE001 — cleanup must not mask the timeout
                logger.debug("terminate_tracked: %s", err)
        self._live_procs.clear()
        return alive

    def emit_output_delta(self, stream: Literal["stdout", "stderr"], text: str) -> None:
        # Kept for the timeout path: whatever a tool streamed is the partial output it can show.
        if text:
            self._output_buffer.append(text)
        if self._bus:
            evt = Event(
                type="tool.output.delta",
                session_id=self._session_id,
                data=ToolOutputDelta(
                    call_id=self.call_id,
                    stream=stream,
                    text=text,
                ),
            )
            self._bus.publish(evt)

    def background(self, proc: Process, label: str = "") -> str:
        """Registers a process with background inbox and returns task_id."""
        import uuid
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        if self._inbox is not None:
            self._inbox.register_task(task_id, proc, label=label)
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Gets status dictionary for a registered background task."""
        if self._inbox is not None:
            return self._inbox.get_status(task_id)
        return None

    def list_tasks(self) -> list[dict[str, Any]]:
        """Lists all registered background tasks."""
        if self._inbox is not None:
            return self._inbox.list_tasks()
        return []

    async def kill_task(self, task_id: str) -> bool:
        """Terminates a running background task."""
        if self._inbox is not None:
            return await self._inbox.kill(task_id)
        return False

    async def send_task_input(self, task_id: str, text: str) -> bool:
        """Sends input text to a running background task."""
        if self._inbox is not None:
            return await self._inbox.send_input(task_id, text)
        return False

    async def wait_task(self, task_id: str, timeout_s: float | None = None) -> Any:
        """Waits for background task to complete and returns exit code."""
        if self._inbox is not None:
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

        # Built-in kernel policies
        self.jail_policy = WorkspaceJailPolicy(jail)
        self.permission_modes_policy = PermissionModesPolicy(settings)

    @property
    def specs(self) -> dict[str, ToolSpec]:
        """Returns map of tool name to ToolSpec for all registered tools."""
        return {name: entry[0] for name, entry in self.registry.tools.items()}

    def register_tool(
        self, spec: ToolSpec, handler: Any, plugin_name: str = "builtin"
    ) -> None:
        """Helper to register a tool with the underlying registry."""
        self.registry._register_tool(spec, handler, plugin_name)

    async def execute(
        self,
        call: ToolCall,
        options: dict[str, Any] | None = None,
        session_id: str = "",
        principal_capabilities: frozenset[Capability] = frozenset(),
        turn_state: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Executes a single tool call through the complete 9-step PolicyGate pipeline.

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
            turn_state=turn_state if turn_state is not None else {},
            bus=self.bus,
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
                        if self.bus:
                            self.bus.publish(
                                Event(
                                    type="tool.decision",
                                    session_id=session_id,
                                    data=ToolDecision(
                                        call_id=call_id,
                                        action="deny",
                                        policy=f"hook:{hook.name}",
                                        risk=risk_val,
                                        reason=reason,
                                    ),
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
            if self.bus:
                self.bus.publish(
                    Event(
                        type="tool.decision",
                        session_id=session_id,
                        data=ToolDecision(
                            call_id=call_id,
                            action="deny",
                            policy=first_deny.policy or "policy",
                            risk=effective_risk_val,
                            reason=first_deny.reason,
                        ),
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
                            metadata={"policy": first_deny.policy},
                        ),
                    )
                )
            return ToolResult.error(err_msg)

        # ---------------------------------------------------------
        # Step 4 & 5: Approvals (when ASK is returned)
        # ---------------------------------------------------------
        if asks:
            reasons = "; ".join(a.reason for a in asks if a.reason)
            if self.bus:
                self.bus.publish(
                    Event(
                        type="tool.decision",
                        session_id=session_id,
                        data=ToolDecision(
                            call_id=call_id,
                            action="ask",
                            policy=asks[0].policy or "policy",
                            risk=effective_risk_val,
                            reason=reasons,
                        ),
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
                                metadata={"approval": "denied"},
                            ),
                        )
                    )
                return ToolResult.error(err_msg)

        # Action is allowed
        if self.bus and not asks:
            self.bus.publish(
                Event(
                    type="tool.decision",
                    session_id=session_id,
                    data=ToolDecision(
                        call_id=call_id,
                        action="allow",
                        policy="policy_gate",
                        risk=effective_risk_val,
                        reason="OK",
                    ),
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
        tool_ctx = ToolContextImpl(
            call_id=call_id,
            workspace=self.registry.workspace,
            data_dir=self.registry.data_dir / "tools" / spec.name,
            capabilities=effective_caps,
            executor=executor,
            bus=self.bus,
            session_id=session_id,
            plugin_name=plugin_name,
            inbox=self.inbox,
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
        task = asyncio.create_task(handler(current_args, tool_ctx))
        timed_out = False
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=spec.timeout_s)
        except TimeoutError:
            timed_out = True
            tool_ctx.cancel_event.set()
            timeout_note = f"Tool '{spec.name}' exceeded {spec.timeout_s:g}s"
            returned_in_grace = False
            try:
                # The handler may still return something useful inside the grace period.
                result = await asyncio.wait_for(task, timeout=grace_s)
                returned_in_grace = True
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
                # Await the cancellation so the handler's own cleanup runs before the turn moves on;
                # gathering without it would leave a pending task nobody owns.
                await asyncio.gather(task, return_exceptions=True)
                result = ToolResult.error(f"{timeout_note} and was cancelled.")
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
        except Exception as err:
            logger.exception(f"Unhandled exception in tool '{spec.name}': {err}")
            result = ToolResult.error(f"{type(err).__name__}: {err}")
        finally:
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

        if self.bus:
            if result.cards:
                for card in result.cards:
                    self.bus.publish(
                        Event(
                            type="ui.card",
                            session_id=session_id,
                            data=UiCard(card=card.to_dict() if hasattr(card, "to_dict") else card),
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

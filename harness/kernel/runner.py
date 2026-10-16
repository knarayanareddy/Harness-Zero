"""
TurnRunner and KernelServices: central execution engine for agent turns.
Fulfills KRN-08 (§5.2, §5.6, §5.8) and absorbs LOOP-01, LOOP-04, MDL-06, MDL-07.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from harness.kernel.breaker import CircuitBreaker
from harness.kernel.bus import EventBus
from harness.kernel.compaction import Compactor, estimate_request_tokens
from harness.kernel.context import ContextAssembler, HookContextImpl, TurnContextImpl
from harness.kernel.economics import economics_snapshot
from harness.kernel.gate import PolicyGate
from harness.kernel.inbox import BackgroundInbox, WakeupEvent
from harness.kernel.registry import PluginRegistry
from harness.kernel.settings import SettingsStore
from harness.sdk.contracts import CostLedger, Provider, SearchIndex, Strategy, TranscriptStore
from harness.sdk.errors import ProviderError, SpendCapExceeded
from harness.sdk.events import (
    CostUpdated,
    Event,
    ModelCompleted,
    ModelNotice,
    ModelStarted,
    ModelTextDelta,
    ModelThinkingDelta,
    ModelToolCall,
    TurnCancelled,
    TurnCompleted,
    TurnError,
    TurnStarted,
)
from harness.sdk.types import (
    Done,
    HookEvent,
    ImageBlock,
    Message,
    ModelParams,
    ModelRequest,
    ProviderNotice,
    RiskClass,
    Role,
    SessionInfo,
    TaskInfo,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolCall,
    ToolCallEnd,
    ToolResult,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    TranscriptRecord,
    TurnResult,
    Usage,
    UsageEvent,
)

logger = logging.getLogger(__name__)


class SingleLoopStrategy:
    """
    Default turn execution strategy.
    Runs iterative loop: assemble context -> compact if needed -> call model -> execute tools.
    Handles background task wake-ups reactively without polling (§5.2, §5.6).
    """

    name: str = "single_loop"

    async def run(self, turn: TurnContextImpl, k: KernelServicesImpl) -> TurnResult:
        max_iterations = turn.options.get("max_iterations", 20)
        total_usage = Usage()
        total_tool_calls = 0
        final_output = ""
        final_thinking = ""
        stop_reason = "end_turn"

        for _ in range(max_iterations):
            if turn.cancel.is_set():
                stop_reason = "cancelled"
                break

            await k.compact_if_needed()
            req = await k.assemble_context()
            assistant_msg = await k.call_model(req)
            await k.record(assistant_msg)

            # Accumulate text and thinking
            for block in assistant_msg.content:
                if isinstance(block, TextBlock):
                    final_output += block.text
                elif isinstance(block, ThinkingBlock):
                    final_thinking += block.text

            if "usage" in assistant_msg.metadata:
                total_usage = total_usage + Usage.from_dict(assistant_msg.metadata["usage"])

            # Extract tool use blocks
            tool_use_blocks = [b for b in assistant_msg.content if isinstance(b, ToolUseBlock)]
            if not tool_use_blocks:
                # No tool calls: check if pending background tasks and wait policy
                pending = k.pending_tasks()
                if pending and turn.options.get("wait_for_background", True):
                    wakeup = await k.wait_for_wakeup(
                        timeout_s=turn.options.get("background_wait_s", 600.0)
                    )
                    if wakeup:
                        wakeup_msg = Message(
                            role=Role.USER,
                            content=[
                                TextBlock(
                                    text=(
                                        f"Background task {wakeup.task_id} ({wakeup.label}) "
                                        f"finished with exit {wakeup.exit_code}:\n{wakeup.tail}"
                                    )
                                )
                            ],
                            metadata={"wakeup": wakeup.task_id},
                        )
                        await k.record(wakeup_msg)
                        continue
                break

            # Execute tool calls
            total_tool_calls += len(tool_use_blocks)
            tool_calls = [
                ToolCall(id=b.id, name=b.name, arguments=b.input)
                for b in tool_use_blocks
            ]
            results = await k.execute_tools(tool_calls)

            # Record tool result blocks
            tool_result_blocks = []
            for b, res in zip(tool_use_blocks, results, strict=False):
                tool_result_blocks.append(
                    ToolResultBlock(
                        tool_use_id=b.id,
                        content=res.content,
                        is_error=res.is_error,
                    )
                )
            result_msg = Message(
                role=Role.USER,
                content=tool_result_blocks,
                metadata={"tool_results": True},
            )
            await k.record(result_msg)
        else:
            # Iteration cap exhausted while the model was still calling tools (see single_loop).
            stop_reason = "max_iterations"

        return TurnResult(
            output=final_output,
            thinking=final_thinking,
            usage=total_usage,
            tool_calls=total_tool_calls,
            stop_reason=stop_reason,
        )


class KernelServicesImpl:
    """
    Concrete implementation of KernelServices passed to Strategies.
    Strictly mediates model calls, policy gate execution, context assembly,
    cost accounting, and background tasks (§5.2, §5.8).
    """

    def __init__(
        self,
        runner: TurnRunner,
        session: SessionInfo,
        turn_id: str,
        options: dict[str, Any],
        cancel: asyncio.Event,
        depth: int = 0,
    ) -> None:
        self.runner = runner
        self.session = session
        self.turn_id = turn_id
        self.options = options
        # Per-turn scratch for hooks (WS-03). Deliberately *not* in `options`: options are recorded
        # on `turn.started`, and a mutable dict shared between gate and hooks is nobody's business
        # but this turn's.
        self.turn_state: dict[str, Any] = {}
        self.cancel = cancel
        self.depth = depth

        self.registry = runner.registry
        self.gate = runner.gate
        self.bus = runner.bus
        self.settings = runner.settings
        self.breaker = runner.breaker
        self.inbox = runner.inbox
        self.assembler = runner.assembler
        self.compactor = runner.compactor

        # Resolve stores and indices for session
        self.session_dir = self.session.workspace / ".harness" / "sessions" / self.session.session_id
        t_store = runner.transcript_store
        if t_store is None and hasattr(self.registry, "stores") and "transcript" in self.registry.stores:
            t_store = self.registry.stores["transcript"][0]
        if t_store is not None and hasattr(t_store, "for_session"):
            self.transcript_store = t_store.for_session(self.session_dir)
        else:
            self.transcript_store = t_store

        s_index = runner.search_index
        if s_index is None and hasattr(self.registry, "stores") and "search" in self.registry.stores:
            s_index = self.registry.stores["search"][0]
        self.search_index = s_index

        c_ledger = runner.cost_ledger
        if c_ledger is None and hasattr(self.registry, "stores") and "cost" in self.registry.stores:
            c_ledger = self.registry.stores["cost"][0]
        self.cost_ledger = c_ledger

        # Turn state
        self.turn_cost_usd: float = 0.0
        self.turn_unknown_pricing: bool = False
        self.transcript_records: list[dict[str, Any]] = []
        self._pricing_cache: dict[str, tuple[float, float]] = {}

        # Spend cap resolution
        spend_cap = options.get("spend_cap_usd")
        if spend_cap is None and self.settings:
            spend_cap = self.settings.get("kernel.security.spend_cap_usd", 5.0)
        self.spend_cap_usd: float | None = float(spend_cap) if spend_cap is not None else None

    @property
    def tools(self) -> list[ToolSpec]:
        """Returns all registered tool specifications."""
        return self.registry.get_all_tool_specs()

    async def emit(self, event: Event) -> None:
        """Publishes an event to the bus if configured."""
        if self.bus:
            self.bus.publish(event)

    def spend_remaining(self) -> float | None:
        """Returns remaining turn spend in USD against spend_cap_usd."""
        if self.spend_cap_usd is None:
            return None
        return max(0.0, self.spend_cap_usd - self.turn_cost_usd)

    def pending_tasks(self) -> list[TaskInfo]:
        """Returns list of actively running background tasks."""
        if not self.inbox:
            return []
        tasks: list[TaskInfo] = []
        for bt in self.inbox._tasks.values():
            if not bt.completed:
                tasks.append(
                    TaskInfo(
                        id=bt.id,
                        label=bt.label,
                        running=True,
                        exit_code=None,
                        tail="\n".join(bt.output_lines[-5:]),
                    )
                )
        return tasks

    async def wait_for_wakeup(self, timeout_s: float = 600.0) -> WakeupEvent | None:
        """Waits for next background process exit event."""
        if not self.inbox:
            return None
        return await self.inbox.wait_for_wakeup(timeout_s)

    async def compact_if_needed(self) -> bool:
        """Triggers compaction if transcript exceeds token budget threshold."""
        if not self.compactor:
            return False
        # Collect current messages
        req = await self.assemble_context()
        all_blocks = req.system + [
            TextBlock(text=m.text()) for m in req.messages
        ]
        # Check budget trigger via compactor
        provider_name = self.options.get("provider", "")
        provider = self._resolve_provider(provider_name)
        return await self.compactor.maybe_compact(
            session_id=self.session.session_id,
            blocks=all_blocks,
            records=self.transcript_records,
            provider=provider,
            model=req.model,
            bus=self.bus,
        )

    async def assemble_context(
        self, *, tools: list[ToolSpec] | None = None
    ) -> ModelRequest:
        """Assembles prompt and history into ModelRequest."""
        prompt = self.options.get("prompt", "")
        media = self.options.get("media", [])
        active_tools = tools if tools is not None else self.tools
        model_name = self.options.get("model") or (
            self.settings.get("kernel.model", "default") if self.settings else "default"
        )

        if self.assembler:
            return await self.assembler.assemble(
                session_id=self.session.session_id,
                history_records=self.transcript_records,
                prompt=prompt,
                media=media,
                tools=active_tools,
                options=self.options,
                model=model_name,
            )

        # Fallback simple assembly
        from harness.kernel.context import records_to_messages
        messages = records_to_messages(self.transcript_records)
        return ModelRequest(
            model=model_name,
            system=[],
            messages=messages,
            tools=active_tools,
            params=ModelParams(),
        )

    def _resolve_provider(self, name: str = "") -> Provider:
        """Resolves LLM Provider by name or default."""
        target_name = name or self.options.get("provider") or (
            self.settings.get("kernel.provider", "") if self.settings else ""
        )
        if target_name and target_name != "default":
            provider = self.registry.get_provider(target_name)
            if provider:
                return provider
            raise ProviderError(
                provider=target_name,
                model=self.options.get("model", "unknown"),
                kind="unconfigured",
                message=f"No provider found matching '{target_name}'. Configure your provider in settings or pass --provider.",
            )

        # No specific provider requested: allow single test/scripted provider if present
        providers = self.registry.get_all_providers()
        if len(providers) == 1 and providers[0].name in ("scripted", "mock", "test"):
            return providers[0]

        raise ProviderError(
            provider="unconfigured",
            model=self.options.get("model", "unknown"),
            kind="unconfigured",
            message="No model provider configured. Set kernel.provider in settings (e.g. 'anthropic', 'openai', 'gemini') or pass --provider.",
        )

    async def _calculate_cost(
        self, provider: Provider, model: str, usage: Usage
    ) -> float | None:
        """Calculates USD cost for model usage; None when the model has no known pricing."""
        if usage.cost_usd is not None:
            return usage.cost_usd

        # Check pricing cache
        if model not in self._pricing_cache:
            try:
                models = await provider.list_models()
                for m in models:
                    if m.id == model and m.input_usd_per_mtok is not None and m.output_usd_per_mtok is not None:
                        self._pricing_cache[model] = (m.input_usd_per_mtok, m.output_usd_per_mtok)
                        break
            except Exception as e:
                logger.debug(f"Could not fetch model pricing for {model}: {e}")

        if model in self._pricing_cache:
            input_price, output_price = self._pricing_cache[model]
            return (usage.input_tokens / 1_000_000.0) * input_price + (
                usage.output_tokens / 1_000_000.0
            ) * output_price

        return None

    async def call_model(self, req: ModelRequest) -> Message:
        """
        Streams model completion events, accumulates assistant message,
        enforces circuit breaker and spend cap, and records usage.
        """
        provider = self._resolve_provider()

        # Enforce circuit breaker
        if self.breaker:
            self.breaker.check(provider.name, req.model)

        est_tokens = estimate_request_tokens(req)
        await self.emit(
            Event(
                type="model.started",
                session_id=self.session.session_id,
                turn_id=self.turn_id,
                data=ModelStarted(
                    provider=provider.name,
                    model=req.model,
                    request_tokens_estimate=est_tokens,
                ),
            )
        )

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls_dict: dict[str, ToolCallEnd] = {}
        usage = Usage()
        stop_reason = "end_turn"

        try:
            async for event in provider.generate(req):
                if self.cancel.is_set():
                    stop_reason = "cancelled"
                    break

                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                    await self.emit(
                        Event(
                            type="model.text.delta",
                            session_id=self.session.session_id,
                            turn_id=self.turn_id,
                            data=ModelTextDelta(text=event.text),
                        )
                    )
                elif isinstance(event, ThinkingDelta):
                    thinking_parts.append(event.text)
                    await self.emit(
                        Event(
                            type="model.thinking.delta",
                            session_id=self.session.session_id,
                            turn_id=self.turn_id,
                            data=ModelThinkingDelta(text=event.text),
                        )
                    )
                elif isinstance(event, ToolCallEnd):
                    tool_calls_dict[event.id] = event
                    await self.emit(
                        Event(
                            type="model.tool_call",
                            session_id=self.session.session_id,
                            turn_id=self.turn_id,
                            data=ModelToolCall(
                                call_id=event.id,
                                name=event.name,
                                arguments=event.arguments,
                            ),
                        )
                    )
                elif isinstance(event, UsageEvent):
                    usage = usage + event.usage
                elif isinstance(event, ProviderNotice):
                    await self.emit(
                        Event(
                            type="model.notice",
                            session_id=self.session.session_id,
                            turn_id=self.turn_id,
                            data=ModelNotice(message=event.message),
                        )
                    )
                elif isinstance(event, Done):
                    stop_reason = event.stop_reason

            if self.breaker:
                self.breaker.record_success(provider.name, req.model)
        except Exception as err:
            if self.breaker:
                self.breaker.record_failure(provider.name, req.model, err)
            raise

        # Cost accounting & spend cap enforcement. Unknown pricing stays None (never 0.0) so the
        # ledger, the economics command and the cost.updated event agree on "unknown".
        call_cost = await self._calculate_cost(provider, req.model, usage)
        usage = Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost_usd=call_cost,
        )
        self.turn_cost_usd += call_cost or 0.0
        if call_cost is None and (usage.input_tokens + usage.output_tokens) > 0:
            self.turn_unknown_pricing = True

        if self.cost_ledger:
            try:
                await self.cost_ledger.record(
                    model=req.model,
                    provider=provider.name,
                    usage=usage,
                    turn_id=self.turn_id,
                    session_id=self.session.session_id,
                )
            except Exception as e:
                logger.warning(f"Error recording cost to ledger: {e}")

        snapshot = await economics_snapshot(
            ledger=self.cost_ledger,
            settings=self.settings,
            session_id=self.session.session_id,
            turn_usd=self.turn_cost_usd,
            turn_unknown_pricing=self.turn_unknown_pricing,
        )
        await self.emit(
            Event(
                type="cost.updated",
                session_id=self.session.session_id,
                turn_id=self.turn_id,
                data=CostUpdated.from_snapshot(snapshot),
            )
        )

        if self.spend_cap_usd is not None and self.turn_cost_usd > self.spend_cap_usd:
            raise SpendCapExceeded(
                f"Turn spend of ${self.turn_cost_usd:.4f} exceeded configured spend cap of ${self.spend_cap_usd:.2f}"
            )

        await self.emit(
            Event(
                type="model.completed",
                session_id=self.session.session_id,
                turn_id=self.turn_id,
                data=ModelCompleted(stop_reason=stop_reason, usage=usage),
            )
        )

        content_blocks = []
        if thinking_parts:
            content_blocks.append(ThinkingBlock(text="".join(thinking_parts)))
        if text_parts:
            content_blocks.append(TextBlock(text="".join(text_parts)))
        for tc in tool_calls_dict.values():
            content_blocks.append(ToolUseBlock(id=tc.id, name=tc.name, input=tc.arguments))

        return Message(
            role=Role.ASSISTANT,
            content=content_blocks,
            metadata={"usage": usage.to_dict(), "stop_reason": stop_reason},
        )

    async def execute_tool(self, call: ToolCall) -> ToolResult:
        """Executes a single tool call via the PolicyGate."""
        return await self.gate.execute(
            call,
            options=self.options,
            session_id=self.session.session_id,
            turn_state=self.turn_state,
        )

    async def execute_tools(self, calls: list[ToolCall]) -> list[ToolResult]:
        """
        Executes a batch of tool calls.
        Executes concurrently in parallel if ALL tools have RiskClass.READ.
        Executes sequentially in order if any tool is WRITE, EXEC, NET, or DESTRUCTIVE (§5.2, §5.8).
        """
        if not calls:
            return []

        # Check risk of all calls
        all_read = True
        for c in calls:
            spec = self.gate.specs.get(c.name)
            if not spec or spec.risk_class != RiskClass.READ:
                all_read = False
                break

        if all_read:
            return list(await asyncio.gather(*(self.execute_tool(c) for c in calls)))

        # Sequential execution
        results: list[ToolResult] = []
        for c in calls:
            if self.cancel.is_set():
                results.append(ToolResult.error("Turn cancelled by user"))
                continue
            res = await self.execute_tool(c)
            results.append(res)
        return results

    async def record(self, msg: Message) -> int:
        """Records message in transcript store and search index."""
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        for b in msg.content:
            if isinstance(b, ToolUseBlock):
                tool_calls.append({
                    "id": b.id,
                    "name": b.name,
                    "arguments": b.input,
                })
            elif isinstance(b, ToolResultBlock):
                tool_results.append({
                    "tool_use_id": b.tool_use_id,
                    "content": [c.to_dict() for c in b.content],
                    "is_error": b.is_error,
                })

        rec = TranscriptRecord(
            role=msg.role.value,
            content=msg.text(),
            timestamp=time.time(),
            tool_calls=tool_calls,
            tool_results=tool_results,
            metadata=msg.metadata,
        ).to_dict()

        self.transcript_records.append(rec)
        step_idx = len(self.transcript_records)

        if self.transcript_store:
            try:
                step_idx = await self.transcript_store.append(rec)
            except Exception as e:
                logger.warning(f"Error appending transcript record: {e}")

        if self.search_index:
            try:
                await self.search_index.index(self.session.session_id, rec)
            except Exception as e:
                logger.warning(f"Error indexing transcript record: {e}")

        return step_idx

    async def spawn_child(
        self,
        *,
        strategy: str = "single_loop",
        prompt: str,
        isolation: str = "none",
        inherit_history: bool = False,
    ) -> TurnResult:
        """
        Spawns a child turn with depth limiting (max 2) and isolated transcript directory (§5.2, §5.8).
        """
        if self.depth >= 2:
            raise RuntimeError("Max child turn depth (2) exceeded")

        child_turn_id = f"child_{uuid.uuid4().hex[:8]}"
        child_workspace = self.session.workspace
        if isolation == "worktree":
            child_workspace = child_workspace / ".harness" / "worktrees" / child_turn_id

        child_session = SessionInfo(
            session_id=f"{self.session.session_id}_{child_turn_id}",
            workspace=child_workspace,
        )

        child_options = dict(self.options)
        child_options["strategy"] = strategy

        return await self.runner.execute_turn(
            session=child_session,
            prompt=prompt,
            options=child_options,
            depth=self.depth + 1,
        )


class TurnRunner:
    """
    Coordinates turn lifecycle from user prompt submission through strategy execution
    to turn completion or cancellation (§5.2, §5.8).
    """

    def __init__(
        self,
        registry: PluginRegistry,
        gate: PolicyGate,
        bus: EventBus | None = None,
        settings: SettingsStore | None = None,
        breaker: CircuitBreaker | None = None,
        inbox: BackgroundInbox | None = None,
        assembler: ContextAssembler | None = None,
        compactor: Compactor | None = None,
        transcript_store: TranscriptStore | None = None,
        search_index: SearchIndex | None = None,
        cost_ledger: CostLedger | None = None,
    ) -> None:
        self.registry = registry
        self.gate = gate
        self.bus = bus
        self.settings = settings
        self.breaker = breaker or CircuitBreaker()
        self.inbox = inbox
        self.assembler = assembler
        self.compactor = compactor
        self.transcript_store = transcript_store
        self.search_index = search_index
        self.cost_ledger = cost_ledger

        # Active turn cancellation tracking
        self._active_cancels: dict[str, asyncio.Event] = {}

    def cancel_turn(self, session_id: str) -> bool:
        """Signals cancellation to an active turn."""
        cancel_evt = self._active_cancels.get(session_id)
        if cancel_evt:
            cancel_evt.set()
            return True
        return False

    async def execute_turn(
        self,
        session: SessionInfo,
        prompt: str,
        media: list[ImageBlock] | None = None,
        options: dict[str, Any] | None = None,
        cancel_event: asyncio.Event | None = None,
        depth: int = 0,
    ) -> TurnResult:
        """
        Executes a full turn lifecycle.
        Steps:
        1. Emits turn.started
        2. Fires USER_PROMPT_SUBMIT hooks (can block or inject additional context)
        3. Records user prompt Message
        4. Runs Strategy (default SingleLoopStrategy) via KernelServices
        5. Fires STOP hooks
        6. Emits turn.completed / turn.error / turn.cancelled
        """
        turn_id = f"turn_{uuid.uuid4().hex[:12]}"
        turn_options = dict(options or {})
        turn_options["prompt"] = prompt
        turn_options["media"] = media or []
        # Kernel-level defaults for the knobs the settings panel owns. A caller may still override
        # per turn (`options={"max_iterations": 3}`); otherwise `kernel.max_tool_iterations` decides
        # — until now the strategies' own literal of 20 always won and the setting did nothing (R38).
        if "max_iterations" not in turn_options and self.settings is not None:
            configured = self.settings.get("kernel.max_tool_iterations")
            if configured is not None:
                turn_options["max_iterations"] = int(configured)

        cancel = cancel_event or asyncio.Event()
        self._active_cancels[session.session_id] = cancel

        start_time = time.time()
        strat_name = turn_options.get("strategy", "single_loop")
        provider_name = turn_options.get("provider", "default")
        model_name = turn_options.get("model", "default")

        if self.bus:
            self.bus.publish(
                Event(
                    type="turn.started",
                    session_id=session.session_id,
                    turn_id=turn_id,
                    data=TurnStarted(
                        prompt_preview=prompt[:120],
                        strategy=strat_name,
                        provider=provider_name,
                        model=model_name,
                        options=turn_options,
                    ),
                )
            )

        # 2. Fire USER_PROMPT_SUBMIT hooks
        hook_ctx = HookContextImpl(session_id=session.session_id, workspace=session.workspace)
        hooks = self.registry.get_hooks_for_event(HookEvent.USER_PROMPT_SUBMIT)
        for h in hooks:
            try:
                outcome = await asyncio.wait_for(
                    h.on(HookEvent.USER_PROMPT_SUBMIT, {"prompt": prompt}, hook_ctx),
                    timeout=h.timeout_s,
                )
                if outcome.action == "block":
                    err_msg = f"Turn blocked by hook '{h.name}': {outcome.reason}"
                    if self.bus:
                        self.bus.publish(
                            Event(
                                type="turn.error",
                                session_id=session.session_id,
                                turn_id=turn_id,
                                data=TurnError(message=err_msg, kind="hook_blocked", retryable=False),
                            )
                        )
                    return TurnResult(output="", error=err_msg, stop_reason="cancelled")
            except Exception as e:
                logger.warning(f"Error in hook '{h.name}': {e}")

        # 3. Instantiate KernelServices and TurnContext
        services = KernelServicesImpl(
            runner=self,
            session=session,
            turn_id=turn_id,
            options=turn_options,
            cancel=cancel,
            depth=depth,
        )

        # Load existing session transcript records into KernelServices
        if services.transcript_store:
            try:
                existing = await services.transcript_store.read(compact=False)
                services.transcript_records = list(existing or [])
            except Exception as e:
                logger.warning(f"Could not load session transcript history: {e}")
                services.transcript_records = []

        turn_ctx = TurnContextImpl(
            session=session,
            turn_id=turn_id,
            prompt=prompt,
            media=media or [],
            options=turn_options,
            settings=self.settings,
            cancel=cancel,
        )

        # Record user Message
        user_blocks = [TextBlock(text=prompt)]
        if media:
            user_blocks.extend(media)
        user_msg = Message(role=Role.USER, content=user_blocks)
        await services.record(user_msg)

        # 4. Resolve Strategy
        strategy: Strategy
        reg_strat = self.registry.get_strategy(strat_name)
        if reg_strat:
            strategy = reg_strat
        else:
            strategy = SingleLoopStrategy()

        # 5. Execute Strategy
        try:
            result = await strategy.run(turn_ctx, services)
        except asyncio.CancelledError:
            if self.bus:
                self.bus.publish(
                    Event(
                        type="turn.cancelled",
                        session_id=session.session_id,
                        turn_id=turn_id,
                        data=TurnCancelled(reason="Turn cancelled by user"),
                    )
                )
            await services.record(
                Message(
                    role=Role.SYSTEM,
                    content=[TextBlock(text="Turn cancelled by user.")],
                    metadata={"type": "notification"},
                )
            )
            return TurnResult(output="Turn cancelled", stop_reason="cancelled")
        except SpendCapExceeded as e:
            if self.bus:
                self.bus.publish(
                    Event(
                        type="turn.error",
                        session_id=session.session_id,
                        turn_id=turn_id,
                        data=TurnError(message=str(e), kind="spend_cap", retryable=False),
                    )
                )
            return TurnResult(output="", error=str(e), stop_reason="spend_cap_exceeded")
        except ProviderError as e:
            logger.warning(f"Provider configuration error: {e}")
            if self.bus:
                self.bus.publish(
                    Event(
                        type="turn.error",
                        session_id=session.session_id,
                        turn_id=turn_id,
                        data=TurnError(message=str(e), kind=e.kind, retryable=False),
                    )
                )
            return TurnResult(output="", error=str(e), stop_reason="error")
        except Exception as e:
            logger.exception(f"Turn execution error: {e}")
            if self.bus:
                self.bus.publish(
                    Event(
                        type="turn.error",
                        session_id=session.session_id,
                        turn_id=turn_id,
                        data=TurnError(message=str(e), kind="runtime_error", retryable=False),
                    )
                )
            return TurnResult(output="", error=str(e), stop_reason="error")
        finally:
            self._active_cancels.pop(session.session_id, None)

        # Check if cancelled during strategy
        if cancel.is_set():
            if self.bus:
                self.bus.publish(
                    Event(
                        type="turn.cancelled",
                        session_id=session.session_id,
                        turn_id=turn_id,
                        data=TurnCancelled(reason="Turn cancelled by user"),
                    )
                )
            await services.record(
                Message(
                    role=Role.SYSTEM,
                    content=[TextBlock(text="Turn cancelled by user.")],
                    metadata={"type": "notification"},
                )
            )
            return TurnResult(output="Turn cancelled", stop_reason="cancelled")

        # 6. STOP hooks
        stop_hooks = self.registry.get_hooks_for_event(HookEvent.STOP)
        for h in stop_hooks:
            try:
                await asyncio.wait_for(
                    h.on(HookEvent.STOP, {"output": result.output}, hook_ctx),
                    timeout=h.timeout_s,
                )
            except Exception as e:
                logger.warning(f"Error in STOP hook '{h.name}': {e}")

        # 7. Emit turn.completed
        duration_ms = int((time.time() - start_time) * 1000)
        if self.bus:
            self.bus.publish(
                Event(
                    type="turn.completed",
                    session_id=session.session_id,
                    turn_id=turn_id,
                    data=TurnCompleted(
                        stop_reason=result.stop_reason,
                        usage=result.usage,
                        duration_ms=duration_ms,
                        tool_calls=result.tool_calls,
                    ),
                )
            )

        return result

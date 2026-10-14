"""
ShellHooksBridgePlugin: Claude Code-compatible shell hooks lifecycle bridge (§10, BRG-01).
"""
from __future__ import annotations

import logging
from typing import Any

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import HookEvent, HookOutcome

from .config import HookRule, load_hooks_from_workspace, parse_claude_hooks_config
from .hook import ShellHook

logger = logging.getLogger(__name__)

ALL_HOOK_EVENTS = frozenset([
    HookEvent.PRE_TOOL_USE,
    HookEvent.POST_TOOL_USE,
    HookEvent.USER_PROMPT_SUBMIT,
    HookEvent.SESSION_START,
    HookEvent.PRE_COMPACT,
    HookEvent.STOP,
    HookEvent.TURN_ERROR,
    HookEvent.NOTIFICATION,
])


class ShellHooksDispatcherHook:
    """
    Master hook dispatcher that evaluates configured shell hook rules.
    Discovers rules from settings and workspace files dynamically per event.
    """

    def __init__(self, static_rules: list[HookRule] | None = None) -> None:
        self.name = "bridge_shell_hooks_dispatcher"
        self.events = ALL_HOOK_EVENTS
        self.event = HookEvent.PRE_TOOL_USE
        self.matcher = None
        self.timeout_s = 60.0
        self.static_rules = static_rules or []

    async def on(
        self, event: HookEvent, payload: dict[str, Any], ctx: Any
    ) -> HookOutcome:
        rules: list[HookRule] = list(self.static_rules)

        # 1. Read dynamically from settings if available
        settings = getattr(ctx, "settings", None)
        if settings:
            raw_setting = settings.get("plugins.shell-hooks.hooks") or settings.get("hooks")
            if raw_setting:
                rules.extend(parse_claude_hooks_config(raw_setting))

        # 2. Read dynamically from workspace if available
        workspace = getattr(ctx, "workspace", None)
        if workspace:
            rules.extend(load_hooks_from_workspace(workspace))

        if not rules:
            return HookOutcome(action="continue")

        current_payload = dict(payload)
        combined_context: list[str] = []
        mutated_args: dict[str, Any] = {}

        for rule in rules:
            if rule.event != event:
                continue

            hook_instance = ShellHook(
                name=rule.name,
                event=rule.event,
                command=rule.command,
                matcher=rule.matcher,
                timeout_s=rule.timeout_s,
            )

            outcome = await hook_instance.on(event, current_payload, ctx)

            # Block immediately stops the chain and returns block
            if outcome.action == "block":
                return outcome

            if outcome.action == "mutate" and outcome.arguments:
                mutated_args.update(outcome.arguments)
                # Update payload for subsequent hooks in chain
                if "call" in current_payload and hasattr(current_payload["call"], "arguments"):
                    current_payload["call"].arguments.update(outcome.arguments)
                elif "arguments" in current_payload and isinstance(current_payload["arguments"], dict):
                    current_payload["arguments"].update(outcome.arguments)

            if outcome.additional_context:
                combined_context.append(outcome.additional_context)

        if mutated_args:
            return HookOutcome(
                action="mutate",
                arguments=mutated_args,
                additional_context="\n".join(combined_context) if combined_context else None,
            )

        if combined_context:
            return HookOutcome(
                action="continue",
                additional_context="\n".join(combined_context),
            )

        return HookOutcome(action="continue")


class ShellHooksBridgePlugin:
    """
    First-party bridge plugin registering Claude Code shell hooks.
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.dispatcher = ShellHooksDispatcherHook()

    async def setup(self, ctx: PluginContext) -> None:
        """Registers the shell hooks dispatcher."""
        ctx.register_hook(self.dispatcher)

    async def teardown(self) -> None:
        """Teardown hooks."""
        pass

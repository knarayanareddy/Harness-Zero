"""
ShellHook: Executes user scripts over stdio with Claude Code-compatible semantics (§10, BRG-01).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from harness.sdk.cards import Card
from harness.sdk.events import Event, HookFired, UiCard
from harness.sdk.types import HookEvent, HookOutcome

from .config import matches_tool

logger = logging.getLogger(__name__)

# Patterns for sensitive environment variables to scrub
_SECRET_PATTERNS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "AUTH",
    "PRIVATE",
)


def _scrub_environment() -> dict[str, str]:
    """Provides a safe, sanitized environment dictionary for hook execution."""
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        k_upper = k.upper()
        if any(pat in k_upper for pat in _SECRET_PATTERNS):
            continue
        if k_upper.startswith("AWS_") or k_upper in ("GH_TOKEN", "GITHUB_TOKEN"):
            continue
        env[k] = v

    # Add standard harness execution headers
    env["CI"] = "1"
    env["HARNESS_AGENT"] = "1"
    env["PAGER"] = "cat"
    env["TERM"] = "dumb"
    return env


class ShellHook:
    """
    Implements the Hook protocol via external shell command execution.
    - Stdin receives JSON payload.
    - Exit 2 signals explicit BLOCK (stderr contains human reason).
    - Exit 0 signals CONTINUE, MUTATE (updatedInput), or ADDITIONAL CONTEXT.
    - Emits hook.fired and notice card on block.
    """

    def __init__(
        self,
        name: str,
        event: HookEvent,
        command: str,
        matcher: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.name = name
        self.events = frozenset([event])
        self.event = event
        self.command = command
        self.matcher = matcher
        self.timeout_s = timeout_s

    async def on(
        self, event: HookEvent, payload: dict[str, Any], ctx: Any
    ) -> HookOutcome:
        """Invoked when the lifecycle hook triggers."""
        # 1. Resolve tool name and check matcher if PRE_TOOL_USE or POST_TOOL_USE
        tool_name = ""
        tool_args: dict[str, Any] = {}
        tool_result: Any = None
        user_prompt: str = ""

        # Extract tool call / spec / prompt from various payload structures
        if "call" in payload:
            call = payload["call"]
            if hasattr(call, "name"):
                tool_name = getattr(call, "name", "") or ""
                tool_args = getattr(call, "arguments", {}) or {}
            elif isinstance(call, dict):
                tool_name = call.get("name", "")
                tool_args = call.get("arguments", {}) or {}
        elif "tool" in payload or "tool_name" in payload:
            tool_name = payload.get("tool") or payload.get("tool_name") or ""
            tool_args = payload.get("arguments") or payload.get("tool_input") or {}

        if "result" in payload:
            tool_result = payload["result"]
        elif "tool_result" in payload:
            tool_result = payload["tool_result"]

        if "prompt" in payload:
            user_prompt = str(payload["prompt"])

        # Check matcher for tool-related hooks
        if event in (HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE):
            if tool_name and not matches_tool(self.matcher, tool_name):
                return HookOutcome(action="continue")

        # 2. Extract workspace and session ID
        workspace = getattr(ctx, "workspace", None)
        if not workspace:
            workspace = Path.cwd()
        elif not isinstance(workspace, Path):
            workspace = Path(str(workspace))

        session_id = getattr(ctx, "session_id", "")

        # 3. Format Claude Code-compatible stdin JSON payload
        claude_event_name = (
            "PreToolUse" if event == HookEvent.PRE_TOOL_USE
            else "PostToolUse" if event == HookEvent.POST_TOOL_USE
            else "UserPromptSubmit" if event == HookEvent.USER_PROMPT_SUBMIT
            else "SessionStart" if event == HookEvent.SESSION_START
            else "PreCompact" if event == HookEvent.PRE_COMPACT
            else "Stop" if event == HookEvent.STOP
            else event.value
        )

        stdin_dict: dict[str, Any] = {
            "event": claude_event_name,
            "session_id": session_id,
            "workspace": str(workspace),
        }

        if tool_name:
            stdin_dict["tool_name"] = tool_name
            stdin_dict["tool_input"] = tool_args
            stdin_dict["arguments"] = tool_args

        if tool_result is not None:
            # Serialize result cleanly
            if hasattr(tool_result, "content"):
                result_text = "\n".join(getattr(b, "text", str(b)) for b in tool_result.content)
            else:
                result_text = str(tool_result)
            stdin_dict["tool_result"] = result_text

        if user_prompt:
            stdin_dict["prompt"] = user_prompt

        stdin_bytes = json.dumps(stdin_dict).encode("utf-8")
        clean_env = _scrub_environment()

        start_time = time.monotonic()
        outcome = HookOutcome(action="continue")

        try:
            proc = await asyncio.create_subprocess_shell(
                self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
                env=clean_env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes),
                timeout=self.timeout_s,
            )
            returncode = proc.returncode

            stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

            # 4. Evaluate exit code and stdout JSON semantics (§10)
            if returncode == 2:
                # Explicit block
                reason = stderr_str or "Blocked by hook rule (exit code 2)"
                outcome = HookOutcome(action="block", reason=reason)
            elif returncode == 0:
                # Success / Continue / Mutate / Add Context
                if stdout_str:
                    try:
                        parsed = json.loads(stdout_str)
                        if isinstance(parsed, dict):
                            decision = parsed.get("decision", "").lower()
                            if decision == "block":
                                outcome = HookOutcome(
                                    action="block",
                                    reason=parsed.get("reason", "Blocked by hook JSON decision"),
                                )
                            else:
                                updated_input = parsed.get("updatedInput") or parsed.get("arguments")
                                add_context = parsed.get("additionalContext") or parsed.get("additional_context")
                                reason = parsed.get("reason", "")

                                if updated_input is not None and isinstance(updated_input, dict):
                                    outcome = HookOutcome(
                                        action="mutate",
                                        arguments=updated_input,
                                        reason=reason,
                                        additional_context=add_context,
                                    )
                                elif add_context is not None:
                                    outcome = HookOutcome(
                                        action="continue",
                                        reason=reason,
                                        additional_context=str(add_context),
                                    )
                    except json.JSONDecodeError:
                        # Non-JSON stdout with exit 0 is accepted as continue
                        pass
            else:
                # Non-zero return code other than 2
                logger.warning(
                    f"Shell hook '{self.name}' exited with code {returncode}: {stderr_str}"
                )
                outcome = HookOutcome(
                    action="continue",
                    reason=f"Hook exited with code {returncode}: {stderr_str}",
                )

        except TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            logger.warning(f"Shell hook '{self.name}' timed out after {self.timeout_s}s")
            outcome = HookOutcome(
                action="continue",
                reason=f"Hook timed out after {self.timeout_s}s",
            )
        except Exception as err:
            logger.exception(f"Shell hook '{self.name}' execution failed: {err}")
            outcome = HookOutcome(action="continue", reason=str(err))

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # 5. Emit hook.fired event and notice card on bus if available
        bus = getattr(ctx, "bus", None)
        if bus:
            try:
                bus.publish(
                    Event(
                        type="hook.fired",
                        session_id=session_id,
                        data=HookFired(
                            hook=self.name,
                            event=event.value,
                            command=self.command,
                            outcome=outcome.to_dict(),
                            duration_ms=elapsed_ms,
                        ),
                    )
                )
                # S13: Reason from stderr shown as a notice card on block
                if outcome.action == "block":
                    bus.publish(
                        Event(
                            type="ui.card",
                            session_id=session_id,
                            data=UiCard(
                                card=Card.notice(
                                    f"Hook '{self.name}' blocked {tool_name or event.value}: {outcome.reason}",
                                    level="error",
                                    title="Hook blocked action",
                                ).to_dict()
                            ),
                        )
                    )
            except Exception as bus_err:
                logger.debug(f"Failed to publish hook event: {bus_err}")

        return outcome

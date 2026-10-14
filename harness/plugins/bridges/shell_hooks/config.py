"""
Claude Code-Compatible Shell Hooks Configuration & Matcher Aliasing (§10, BRG-01, D16).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.sdk.types import HookEvent

logger = logging.getLogger(__name__)

# D16: Mapping from Harness tool names to Claude Code tool names / aliases
TOOL_ALIASES: dict[str, list[str]] = {
    "run_command": ["Bash", "bash", "sh", "exec", "shell"],
    "view_file": ["Read", "read", "View", "view"],
    "write_to_file": ["Write", "write"],
    "edit_file": ["Edit", "edit"],
    "search_codebase": ["Grep", "grep", "Search", "search"],
    "manage_task": ["Task", "task"],
    "search_memory": ["Memory", "memory"],
}

EVENT_NAME_MAP: dict[str, HookEvent] = {
    "pretooluse": HookEvent.PRE_TOOL_USE,
    "pre_tool_use": HookEvent.PRE_TOOL_USE,
    "pre-tool-use": HookEvent.PRE_TOOL_USE,
    "posttooluse": HookEvent.POST_TOOL_USE,
    "post_tool_use": HookEvent.POST_TOOL_USE,
    "post-tool-use": HookEvent.POST_TOOL_USE,
    "userpromptsubmit": HookEvent.USER_PROMPT_SUBMIT,
    "user_prompt_submit": HookEvent.USER_PROMPT_SUBMIT,
    "user-prompt-submit": HookEvent.USER_PROMPT_SUBMIT,
    "sessionstart": HookEvent.SESSION_START,
    "session_start": HookEvent.SESSION_START,
    "session-start": HookEvent.SESSION_START,
    "precompact": HookEvent.PRE_COMPACT,
    "pre_compact": HookEvent.PRE_COMPACT,
    "pre-compact": HookEvent.PRE_COMPACT,
    "stop": HookEvent.STOP,
    "turnerror": HookEvent.TURN_ERROR,
    "turn_error": HookEvent.TURN_ERROR,
    "turn-error": HookEvent.TURN_ERROR,
    "notification": HookEvent.NOTIFICATION,
}


@dataclass(slots=True)
class HookRule:
    """Represents a single executable shell hook rule."""
    name: str
    event: HookEvent
    command: str
    matcher: str | None = None
    timeout_s: float = 30.0


def normalize_event_name(raw_name: str) -> HookEvent | None:
    """Maps Claude Code or lowercase string to HookEvent enum."""
    normalized = raw_name.strip().lower().replace("_", "").replace("-", "")
    for key, event in EVENT_NAME_MAP.items():
        if key.replace("_", "").replace("-", "") == normalized:
            return event
    return None


def matches_tool(matcher: str | None, tool_name: str) -> bool:
    """Evaluates whether matcher regex matches tool_name or any of its D16 aliases."""
    if not matcher or matcher.strip() in ("", "*", ".*"):
        return True

    # Candidates: actual tool name + Claude Code aliases
    candidates = [tool_name] + TOOL_ALIASES.get(tool_name, [])

    try:
        pattern = re.compile(matcher, re.IGNORECASE)
        for c in candidates:
            if pattern.search(c):
                return True
    except re.error as err:
        logger.warning(f"Invalid regex in hook matcher '{matcher}': {err}")
        return False

    return False


def parse_claude_hooks_config(raw_config: Any) -> list[HookRule]:
    """
    Parses Claude Code compatible hooks config into HookRule instances.
    Supports nested format:
      {"hooks": {"PreToolUse": [{"matcher": "Bash|Write", "hooks": [{"command": "...", "timeout": 30}]}]}}
    as well as flattened dictionaries and arrays.
    """
    rules: list[HookRule] = []
    if not raw_config:
        return rules

    if isinstance(raw_config, str):
        try:
            raw_config = json.loads(raw_config)
        except Exception:
            return rules

    # Unpack root "hooks" wrapper if present
    data = raw_config.get("hooks", raw_config) if isinstance(raw_config, dict) else raw_config

    if isinstance(data, dict):
        for raw_event, rule_groups in data.items():
            event = normalize_event_name(raw_event)
            if not event:
                continue

            if not isinstance(rule_groups, list):
                rule_groups = [rule_groups]

            for group_idx, group in enumerate(rule_groups):
                if not isinstance(group, dict):
                    continue

                matcher = group.get("matcher")

                # Nested Claude Code style: {"hooks": [{"command": "...", "timeout": ...}]}
                nested_hooks = group.get("hooks")
                if isinstance(nested_hooks, list):
                    for h_idx, h in enumerate(nested_hooks):
                        if not isinstance(h, dict):
                            continue
                        cmd = h.get("command")
                        if not cmd:
                            continue
                        timeout = float(h.get("timeout", 30.0))
                        rule_name = h.get("name") or f"{raw_event}_{group_idx}_{h_idx}"
                        rules.append(
                            HookRule(
                                name=rule_name,
                                event=event,
                                command=cmd,
                                matcher=matcher,
                                timeout_s=timeout,
                            )
                        )
                # Flat style: {"matcher": "...", "command": "...", "timeout": ...}
                elif "command" in group:
                    cmd = group["command"]
                    timeout = float(group.get("timeout", 30.0))
                    rule_name = group.get("name") or f"{raw_event}_{group_idx}"
                    rules.append(
                        HookRule(
                            name=rule_name,
                            event=event,
                            command=cmd,
                            matcher=matcher,
                            timeout_s=timeout,
                        )
                    )

    elif isinstance(data, list):
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            event = normalize_event_name(item.get("event", ""))
            cmd = item.get("command")
            if event and cmd:
                timeout = float(item.get("timeout", 30.0))
                rule_name = item.get("name") or f"hook_{idx}"
                rules.append(
                    HookRule(
                        name=rule_name,
                        event=event,
                        command=cmd,
                        matcher=item.get("matcher"),
                        timeout_s=timeout,
                    )
                )

    return rules


def load_hooks_from_workspace(workspace_dir: Path) -> list[HookRule]:
    """Discovers .claude/hooks.json or .harness/hooks.json inside workspace."""
    candidate_paths = [
        workspace_dir / ".harness" / "hooks.json",
        workspace_dir / ".claude" / "hooks.json",
    ]
    for path in candidate_paths:
        if path.is_file():
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
                return parse_claude_hooks_config(content)
            except Exception as err:
                logger.warning(f"Failed to read hooks configuration from {path}: {err}")
    return []

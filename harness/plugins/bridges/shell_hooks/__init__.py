"""
Claude Code-compatible Shell Hooks Bridge (§10, BRG-01).
"""
from __future__ import annotations

from .config import HookRule, matches_tool, parse_claude_hooks_config
from .hook import ShellHook
from .plugin import ShellHooksBridgePlugin, ShellHooksDispatcherHook

__all__ = [
    "HookRule",
    "matches_tool",
    "parse_claude_hooks_config",
    "ShellHook",
    "ShellHooksBridgePlugin",
    "ShellHooksDispatcherHook",
]

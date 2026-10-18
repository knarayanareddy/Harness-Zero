"""
Repeat-call reminder hook plugin entry point (WS-03).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .hook import RepeatCallReminderHook, RepeatSettings


class RepeatCallReminderPlugin:
    """Registers the spin detector; its counters live in per-turn state, never in this object."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        s = ctx.settings
        cfg = RepeatSettings(
            enabled=bool(s.get("enabled", True)),
            repeat_after=int(s.get("repeat_after", 3) or 3),
            block_after=int(s.get("block_after", 0) or 0),
            exempt_tools=tuple(s.get("exempt_tools", ["manage_task", "view_file"]) or []),
        )
        ctx.register_hook(RepeatCallReminderHook(cfg))

    async def teardown(self) -> None:
        pass

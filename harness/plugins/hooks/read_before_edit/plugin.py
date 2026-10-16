"""
Read-before-edit hook plugin entry point (WS-03).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .hook import ReadBeforeEditHook, ReadBeforeEditSettings


class ReadBeforeEditPlugin:
    """Registers the read-ledger guard for surgical edits."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        s = ctx.settings
        cfg = ReadBeforeEditSettings(
            # `mode` is the single switch: `off` means disabled. A second `enabled`
            # boolean next to it would be two ways to say the same thing, and they can disagree.
            mode=str(s.get("mode", "block") or "block"),
            exempt_globs=tuple(s.get("exempt_globs", []) or []),
            guarded_tools=tuple(s.get("guarded_tools", ["edit_file", "write_to_file"]) or []),
            read_tools=tuple(s.get("read_tools", ["view_file"]) or []),
        )
        ctx.register_hook(ReadBeforeEditHook(cfg, ctx.workspace))

    async def teardown(self) -> None:
        pass

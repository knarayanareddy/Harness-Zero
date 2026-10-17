"""Lifecycle entry point for the structured ask_user tool plugin."""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .tools import ASK_USER_SPEC, handle_ask_user


class AskUserPlugin:
    """Registers the read-only structured-question tool."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register_tool(ASK_USER_SPEC, handle_ask_user)

    async def teardown(self) -> None:
        pass

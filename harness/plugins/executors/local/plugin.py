"""
Local Executor Plugin Entry Point (§2, §4, PLG-02).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .executor import LocalExecutor


class LocalExecutorPlugin:
    """
    Plugin lifecycle class registering the default LocalExecutor.
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.executor: LocalExecutor | None = None

    async def setup(self, ctx: PluginContext) -> None:
        """Instantiates and registers LocalExecutor with the kernel."""
        self.executor = LocalExecutor(default_cwd=ctx.workspace)
        ctx.register_executor(self.executor)

    async def teardown(self) -> None:
        """Cleans up executor resources."""
        self.executor = None

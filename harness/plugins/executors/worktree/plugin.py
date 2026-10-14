"""
Worktree Executor Plugin Entry Point (§4, PLG-18).
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .executor import WorktreeExecutor


class WorktreeExecutorPlugin:
    """Plugin lifecycle class registering WorktreeExecutor."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.executor: WorktreeExecutor | None = None

    async def setup(self, ctx: PluginContext) -> None:
        """Instantiates and registers WorktreeExecutor with the kernel."""
        self.executor = WorktreeExecutor(repo_root=ctx.workspace, default_cwd=ctx.workspace)
        ctx.register_executor(self.executor)

    async def teardown(self) -> None:
        """Cleans up worktree executor resources."""
        self.executor = None

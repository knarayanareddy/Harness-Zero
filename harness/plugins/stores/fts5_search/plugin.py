"""
FTS5 Search Store plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .index import FTS5SearchIndex


class Fts5SearchPlugin:
    """Plugin lifecycle class registering FTS5SearchIndex."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.store: FTS5SearchIndex | None = None

    async def setup(self, ctx: PluginContext) -> None:
        db_path = ctx.data_dir / "memory.db"
        self.store = FTS5SearchIndex(db_path)
        sessions_dir = ctx.data_dir / "sessions"
        self.store.maybe_reindex_from_sessions(sessions_dir)
        ctx.register_store("search", self.store)

    async def teardown(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None

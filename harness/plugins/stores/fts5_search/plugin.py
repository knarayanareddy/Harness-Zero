"""
FTS5 Search Store plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest
from harness.sdk.paths import memory_db_path, sessions_dir

from .index import FTS5SearchIndex


class Fts5SearchPlugin:
    """Plugin lifecycle class registering FTS5SearchIndex."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.store: FTS5SearchIndex | None = None

    async def setup(self, ctx: PluginContext) -> None:
        # Shared workspace memory database (see harness.sdk.paths): the
        # `remember`/`search_memory` tools and the `memories` context source
        # open the same file, so it must NOT live under the plugin-private
        # data_dir.
        db_path = memory_db_path(ctx.workspace)
        self.store = FTS5SearchIndex(db_path)
        self.store.maybe_reindex_from_sessions(sessions_dir(ctx.workspace))
        ctx.register_store("search", self.store)

    async def teardown(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None

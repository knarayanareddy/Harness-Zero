"""
Cost ledger store plugin entry point.
"""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .ledger import FileCostLedger


class CostLedgerPlugin:
    """Plugin lifecycle class registering FileCostLedger."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        ledger = FileCostLedger(ctx.data_dir / "economics.json")
        ctx.register_store("cost", ledger)

    async def teardown(self) -> None:
        pass

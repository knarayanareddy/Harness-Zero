"""
ACPBridgePlugin: Registers Agent Client Protocol bridge capabilities (§10, BRG-04).
"""
from __future__ import annotations

import logging

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

logger = logging.getLogger(__name__)


class ACPBridgePlugin:
    """
    First-party bridge plugin defining ACP frontend capabilities.
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest

    async def setup(self, ctx: PluginContext) -> None:
        """Setup hook for ACP plugin."""
        pass

    async def teardown(self) -> None:
        """Teardown hook for ACP plugin."""
        pass

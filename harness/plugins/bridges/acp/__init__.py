"""
Agent Client Protocol (ACP) Bridge Frontend (§10, BRG-04).
"""
from __future__ import annotations

from .plugin import ACPBridgePlugin
from .protocol import ACPNotification, ACPRequest, ACPResponse
from .server import ACPServer, run_acp_server_from_stdio

__all__ = [
    "ACPBridgePlugin",
    "ACPNotification",
    "ACPRequest",
    "ACPResponse",
    "ACPServer",
    "run_acp_server_from_stdio",
]

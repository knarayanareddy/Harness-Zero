"""
Agent Client Protocol (ACP) Message Types & JSON-RPC 2.0 Definitions (§10, BRG-04).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ACPRequest:
    id: int | str
    method: str
    params: dict[str, Any]


@dataclass(slots=True)
class ACPResponse:
    id: int | str | None
    result: Any = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error is not None:
            msg["error"] = self.error
        else:
            msg["result"] = self.result
        return msg


@dataclass(slots=True)
class ACPNotification:
    method: str
    params: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "method": self.method,
            "params": self.params,
        }

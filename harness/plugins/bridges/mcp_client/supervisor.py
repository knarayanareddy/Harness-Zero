"""Reconnect backoff policy shared by MCP stdio and Streamable HTTP clients."""
from __future__ import annotations

import random


class ReconnectSupervisor:
    """Calculates bounded exponential backoff with jitter for reconnect attempts."""

    def __init__(
        self,
        *,
        max_attempts: int = 10,
        initial_backoff_s: float = 1.0,
        max_backoff_s: float = 60.0,
        jitter_fraction: float = 0.2,
    ) -> None:
        self.max_attempts = max(0, int(max_attempts))
        self.initial_backoff_s = max(0.0, float(initial_backoff_s))
        self.max_backoff_s = max(0.0, float(max_backoff_s))
        self.jitter_fraction = min(1.0, max(0.0, float(jitter_fraction)))

    def delay(self, attempt: int) -> float:
        """Return a capped delay for a 1-based reconnect attempt."""
        if attempt < 1:
            raise ValueError("reconnect attempt numbers start at 1")
        ceiling = min(self.max_backoff_s, self.initial_backoff_s * (2 ** (attempt - 1)))
        if ceiling <= 0 or self.jitter_fraction == 0:
            return ceiling
        spread = ceiling * self.jitter_fraction
        jittered = ceiling - spread + random.random() * spread * 2
        return min(self.max_backoff_s, max(0.0, jittered))

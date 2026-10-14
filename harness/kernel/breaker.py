"""
CircuitBreaker: Per-(provider, model) circuit breaker.
Transitions between CLOSED, OPEN, and HALF_OPEN states.
Fulfills KRN-08 (§5.2, §5.8).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

from harness.sdk.errors import ProviderError

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class BreakerEntry:
    provider: str
    model: str
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    half_open_in_flight: bool = False


class CircuitBreaker:
    """
    Per-(provider, model) circuit breaker.
    Opens after max_failures consecutive failures.
    Remains OPEN until reset_timeout_s expires, transitioning to HALF_OPEN
    for a trial call.
    """

    def __init__(
        self,
        max_failures: int = 3,
        reset_timeout_s: float = 30.0,
    ) -> None:
        self.max_failures = max_failures
        self.reset_timeout_s = reset_timeout_s
        self._entries: dict[tuple[str, str], BreakerEntry] = {}

    def _get_entry(self, provider: str, model: str) -> BreakerEntry:
        key = (provider, model)
        if key not in self._entries:
            self._entries[key] = BreakerEntry(provider=provider, model=model)
        return self._entries[key]

    def check(self, provider: str, model: str) -> None:
        """
        Checks whether calls to (provider, model) are permitted.
        Raises ProviderError(kind="unavailable") if the breaker is OPEN.
        """
        entry = self._get_entry(provider, model)
        now = time.time()

        if entry.state == CircuitState.OPEN:
            if (now - entry.opened_at) >= self.reset_timeout_s:
                entry.state = CircuitState.HALF_OPEN
                entry.half_open_in_flight = True
                logger.info(
                    f"Circuit breaker for [{provider}/{model}] transitioned to HALF_OPEN trial"
                )
            else:
                remaining = self.reset_timeout_s - (now - entry.opened_at)
                raise ProviderError(
                    provider=provider,
                    model=model,
                    kind="unavailable",
                    retryable=True,
                    message=(
                        f"Circuit breaker is OPEN after {entry.consecutive_failures} consecutive failures. "
                        f"Retry permitted in {remaining:.1f}s."
                    ),
                )
        elif entry.state == CircuitState.HALF_OPEN:
            # Already in trial call
            pass

    def record_success(self, provider: str, model: str) -> None:
        """Records a successful call, resetting failure counts and closing the breaker."""
        entry = self._get_entry(provider, model)
        if entry.state != CircuitState.CLOSED:
            logger.info(
                f"Circuit breaker for [{provider}/{model}] reset from {entry.state.value} to CLOSED"
            )
        entry.state = CircuitState.CLOSED
        entry.consecutive_failures = 0
        entry.opened_at = 0.0
        entry.half_open_in_flight = False

    def record_failure(
        self,
        provider: str,
        model: str,
        error: Exception | None = None,
    ) -> None:
        """Records a failure, transitioning to OPEN if threshold reached."""
        entry = self._get_entry(provider, model)
        entry.consecutive_failures += 1

        if entry.state == CircuitState.HALF_OPEN:
            entry.state = CircuitState.OPEN
            entry.opened_at = time.time()
            entry.half_open_in_flight = False
            logger.warning(
                f"Circuit breaker trial failed for [{provider}/{model}]; reopened"
            )
        elif entry.consecutive_failures >= self.max_failures:
            entry.state = CircuitState.OPEN
            entry.opened_at = time.time()
            logger.warning(
                f"Circuit breaker OPENED for [{provider}/{model}] after {entry.consecutive_failures} failures"
            )

    def get_state(self, provider: str, model: str) -> CircuitState:
        """Returns the current state for (provider, model)."""
        entry = self._get_entry(provider, model)
        if entry.state == CircuitState.OPEN and (time.time() - entry.opened_at) >= self.reset_timeout_s:
            return CircuitState.HALF_OPEN
        return entry.state

    def reset(self, provider: str | None = None, model: str | None = None) -> None:
        """Resets breaker state for a specific pair or all pairs."""
        if provider and model:
            self._entries.pop((provider, model), None)
        else:
            self._entries.clear()

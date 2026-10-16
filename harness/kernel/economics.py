"""One place that turns the cost ledger + settings into an ``EconomicsSnapshot``.

Used by ``Commands.economics`` (and therefore ``GET /health``) and by the turn runner's
``cost.updated`` event, so every consumer sees the same shape and the same numbers.
"""
from __future__ import annotations

from typing import Any

from harness.sdk.types import EconomicsSnapshot, Usage

SPEND_CAP_SETTING = "kernel.security.spend_cap_usd"
DEFAULT_SPEND_CAP_USD = 5.0


async def economics_snapshot(
    *,
    ledger: Any | None,
    settings: Any | None,
    session_id: str | None,
    turn_usd: float = 0.0,
    turn_unknown_pricing: bool = False,
) -> EconomicsSnapshot:
    """Builds the snapshot for ``session_id`` (session totals + workspace lifetime total).

    ``turn_usd`` / ``turn_unknown_pricing`` describe the turn in flight (the runner knows them;
    the command passes zero/false). Costs are lower bounds when ``unknown_pricing`` is true.
    """
    cap_raw = settings.get(SPEND_CAP_SETTING, DEFAULT_SPEND_CAP_USD) if settings is not None else None
    cap_usd = float(cap_raw) if cap_raw is not None else None

    session_usage = Usage()
    lifetime_usage = Usage()
    if ledger is not None:
        if session_id:
            session_usage = await ledger.totals(session_id=session_id)
        lifetime_usage = await ledger.totals()
        if not session_id:
            session_usage = lifetime_usage

    tokens = session_usage.input_tokens + session_usage.output_tokens
    unknown = bool(turn_unknown_pricing) or (session_usage.cost_usd is None and tokens > 0)
    return EconomicsSnapshot(
        turn_usd=float(turn_usd),
        session_usd=float(session_usage.cost_usd or 0.0),
        lifetime_usd=float(lifetime_usage.cost_usd or 0.0),
        cap_usd=cap_usd,
        unknown_pricing=unknown,
        usage=session_usage,
    )

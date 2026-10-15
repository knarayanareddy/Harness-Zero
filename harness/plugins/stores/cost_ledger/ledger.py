"""
Persistent Cost Ledger and Token Economics (§5.2, §5.8, PLG-08).
Absorbs MDL-06.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.sdk.types import Usage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float = 0.0


# Pricing table ($ per 1M tokens). If a model is not listed, cost is None.
MODEL_PRICING: dict[str, ModelPrice] = {
    # Anthropic
    "claude-3-7-sonnet": ModelPrice(3.0, 15.0, 0.30),
    "claude-3-5-sonnet-latest": ModelPrice(3.0, 15.0, 0.30),
    "claude-3-5-sonnet-20241022": ModelPrice(3.0, 15.0, 0.30),
    "claude-3-5-haiku-latest": ModelPrice(0.80, 4.0, 0.08),
    "claude-3-opus-20240229": ModelPrice(15.0, 75.0, 1.50),
    # OpenAI
    "gpt-4o": ModelPrice(2.50, 10.0, 1.25),
    "gpt-4o-mini": ModelPrice(0.15, 0.60, 0.075),
    "o1": ModelPrice(15.0, 60.0, 7.50),
    "o3-mini": ModelPrice(1.10, 4.40, 0.55),
    # Google
    "gemini-2.0-flash": ModelPrice(0.10, 0.40, 0.025),
    "gemini-1.5-pro": ModelPrice(1.25, 5.0, 0.3125),
    # DeepSeek
    "deepseek-chat": ModelPrice(0.14, 0.28, 0.014),
    "deepseek-reasoner": ModelPrice(0.55, 2.19, 0.14),
    # Local / mock models are zero-cost
    "mock": ModelPrice(0.0, 0.0, 0.0),
    "ollama": ModelPrice(0.0, 0.0, 0.0),
}


class FileCostLedger:
    """
    Persists token usage and cost accounting to an economics.json file.
    Calculates exact dollar expenditures with prompt caching discounts.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.is_dir():
            self.path = self.path / "economics.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._records: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self._records = data
                elif isinstance(data, dict):
                    self._records = data.get("records", [])
        except Exception as err:
            logger.warning(f"Error reading economics ledger at {self.path}: {err}")

    def _calculate_cost(self, model: str, usage: Usage) -> float | None:
        pricing = MODEL_PRICING.get(model)
        if not pricing:
            # Check lowercase or stripped variant
            cleaned = model.lower().split("/")[-1]
            pricing = MODEL_PRICING.get(cleaned)

        if not pricing:
            return None

        in_cost = (usage.input_tokens / 1_000_000.0) * pricing.input_per_million
        out_cost = (usage.output_tokens / 1_000_000.0) * pricing.output_per_million
        cache_cost = (usage.cache_read_tokens / 1_000_000.0) * pricing.cache_read_per_million
        return in_cost + out_cost + cache_cost

    async def record(
        self, *, model: str, provider: str, usage: Usage, turn_id: str, session_id: str | None = None
    ) -> None:
        """Records a model usage event, calculates cost, and writes to economics.json."""
        cost = self._calculate_cost(model, usage)
        if cost is not None and usage.cost_usd is None:
            usage.cost_usd = cost

        rec = {
            "turn_id": turn_id,
            "session_id": session_id,
            "provider": provider,
            "model": model,
            "timestamp": time.time(),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cost_usd": usage.cost_usd,
        }

        async with self._lock:
            self._records.append(rec)
            data_to_write = {
                "total_records": len(self._records),
                "last_updated": time.time(),
                "records": self._records,
            }

            def _save():
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(data_to_write, f, indent=2)

            await asyncio.to_thread(_save)

    async def totals(self, *, session_id: str | None = None) -> Usage:
        """Returns accumulated usage and cost for ``session_id`` (or for every session when None).

        ``cost_usd`` is None when no record in scope had known pricing.
        """
        async with self._lock:
            total = Usage()
            total_cost: float = 0.0
            has_known_cost = False

            for r in self._records:
                if session_id is not None and r.get("session_id") != session_id:
                    continue
                total.input_tokens += r.get("input_tokens", 0)
                total.output_tokens += r.get("output_tokens", 0)
                total.cache_read_tokens += r.get("cache_read_tokens", 0)
                total.cache_write_tokens += r.get("cache_write_tokens", 0)
                total.reasoning_tokens += r.get("reasoning_tokens", 0)

                cost = r.get("cost_usd")
                if cost is not None:
                    total_cost += cost
                    has_known_cost = True

            total.cost_usd = total_cost if has_known_cost else None
            return total

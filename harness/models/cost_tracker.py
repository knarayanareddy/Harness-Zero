"""
Token Economics & Cost Tracker (Cline Agent Pattern).
Calculates exact dollar expenditures, prompt cache savings, and usage metrics per session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ModelPricing:
    input_cost_per_million: float
    cache_read_cost_per_million: float
    output_cost_per_million: float


# Industry standard pricing as of current models ($ per 1M tokens)
MODEL_PRICING_TABLE: Dict[str, ModelPricing] = {
    "claude-3-5-sonnet-latest": ModelPricing(3.0, 0.30, 15.0),
    "claude-3-7-sonnet": ModelPricing(3.0, 0.30, 15.0),
    "claude-3-5-haiku-latest": ModelPricing(0.80, 0.08, 4.0),
    "gpt-4o": ModelPricing(2.50, 1.25, 10.0),
    "gpt-4o-mini": ModelPricing(0.15, 0.075, 0.60),
    "o1": ModelPricing(15.0, 7.50, 60.0),
    "o3-mini": ModelPricing(1.10, 0.55, 4.40),
    "gemini-2.0-flash": ModelPricing(0.10, 0.025, 0.40),
    "gemini-1.5-pro": ModelPricing(1.25, 0.3125, 5.0),
    "deepseek-chat": ModelPricing(0.14, 0.014, 0.28),
    "deepseek-reasoner": ModelPricing(0.55, 0.14, 2.19),
    # Local models: 100% free!
    "local": ModelPricing(0.0, 0.0, 0.0),
    "mock": ModelPricing(0.0, 0.0, 0.0),
}


@dataclass
class UsageSnapshot:
    prompt_tokens: int
    cache_read_tokens: int
    completion_tokens: int
    total_cost_usd: float
    dollars_saved_usd: float


class TokenCostTracker:
    def __init__(self):
        self.total_input_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.total_saved_usd: float = 0.0
        self.history: List[Dict[str, Any]] = []

    def record_turn(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
    ) -> UsageSnapshot:
        pricing = MODEL_PRICING_TABLE.get(model, MODEL_PRICING_TABLE.get("claude-3-5-sonnet-latest"))

        # Actual cost
        input_cost = (input_tokens / 1_000_000.0) * pricing.input_cost_per_million
        cache_cost = (cache_read_tokens / 1_000_000.0) * pricing.cache_read_cost_per_million
        output_cost = (output_tokens / 1_000_000.0) * pricing.output_cost_per_million
        turn_cost = input_cost + cache_cost + output_cost

        # Dollars saved by prompt cache
        full_price_without_cache = (cache_read_tokens / 1_000_000.0) * pricing.input_cost_per_million
        dollars_saved = max(0.0, full_price_without_cache - cache_cost)

        self.total_input_tokens += input_tokens
        self.total_cache_read_tokens += cache_read_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += turn_cost
        self.total_saved_usd += dollars_saved

        snapshot = UsageSnapshot(
            prompt_tokens=input_tokens,
            cache_read_tokens=cache_read_tokens,
            completion_tokens=output_tokens,
            total_cost_usd=turn_cost,
            dollars_saved_usd=dollars_saved,
        )
        self.history.append({"model": model, "snapshot": snapshot.__dict__})
        return snapshot

    def get_summary(self) -> Dict[str, Any]:
        return {
            "turns_count": len(self.history),
            "total_input_tokens": self.total_input_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": self.total_cost_usd,
            "total_saved_usd": self.total_saved_usd,
            "history": self.history,
        }

    def render_summary(self) -> str:
        cache_pct = 0.0
        total_prompts = self.total_input_tokens + self.total_cache_read_tokens
        if total_prompts > 0:
            cache_pct = (self.total_cache_read_tokens / total_prompts) * 100.0

        return f"""## Session Token Economics:
- Input Tokens: {self.total_input_tokens:,}
- Cached Tokens Read: {self.total_cache_read_tokens:,} ({cache_pct:.1f}% cache hit rate)
- Output Tokens: {self.total_output_tokens:,}
- Cumulative Cost: ${self.total_cost_usd:.4f} USD
- Dollars Saved via Prompt Cache: ${self.total_saved_usd:.4f} USD"""

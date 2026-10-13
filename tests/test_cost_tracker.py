import pytest
from harness.models.cost_tracker import TokenCostTracker, MODEL_PRICING_TABLE

def test_token_cost_tracker_economics():
    tracker = TokenCostTracker()

    # Track usage with prompt caching (Claude 3.5 Sonnet)
    usage = tracker.record_turn(
        model="claude-3-5-sonnet-latest",
        input_tokens=10000,
        output_tokens=1000,
        cache_read_tokens=8000
    )

    assert usage.total_cost_usd > 0
    assert usage.dollars_saved_usd > 0
    assert tracker.total_cost_usd > 0
    assert tracker.total_saved_usd > 0

    summary = tracker.get_summary()
    assert summary["turns_count"] == 1
    assert summary["total_cost_usd"] > 0
    assert summary["total_saved_usd"] > 0

def test_local_model_free():
    tracker = TokenCostTracker()
    usage = tracker.record_turn(
        model="local",
        input_tokens=50000,
        output_tokens=5000
    )
    assert usage.total_cost_usd == 0.0
    assert tracker.total_cost_usd == 0.0

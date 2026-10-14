"""
Unit and conformance tests for store plugins (PLG-08).
Absorbs LOOP-05 and MDL-06.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.kernel.bus import EventBus
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.stores.cost_ledger.ledger import FileCostLedger
from harness.plugins.stores.jsonl_transcript.store import JsonlTranscriptStore
from harness.sdk.testing.kits import StoreKit
from harness.sdk.types import Usage


# -----------------------------------------------------------------------------
# 1. StoreKit Conformance
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_storekit_conformance(tmp_path: Path):
    kit = StoreKit()
    store = JsonlTranscriptStore(tmp_path / "test_session")
    await kit.test_append_read_round_trip(store)


# -----------------------------------------------------------------------------
# 2. Dual-Log and Tolerant Transcript Tests (LOOP-05, MDL-03)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_transcript_dual_log_and_offsets(tmp_path: Path):
    sess_dir = tmp_path / "sess_dual"
    store = JsonlTranscriptStore(sess_dir)

    long_text = "A" * 2000
    rec = {
        "type": "tool_result",
        "content": long_text,
        "metadata": {
            "call_id": "call_abc123",
            "tool_name": "run_command",
            "is_error": False,
        },
    }

    step_idx = await store.append(rec)
    assert step_idx == 1

    # Read compact
    compact_records = await store.read(compact=True)
    assert len(compact_records) == 1
    c_rec = compact_records[0]
    assert "[TRUNCATED]" in c_rec["content"]
    assert "content" in c_rec["truncated_fields"]
    assert c_rec["call_id"] == "call_abc123"
    assert c_rec["tool_name"] == "run_command"
    assert c_rec["is_error"] is False
    assert "full_offset" in c_rec

    # Read full
    full_records = await store.read(compact=False)
    assert len(full_records) == 1
    f_rec = full_records[0]
    assert f_rec["content"] == long_text


@pytest.mark.asyncio
async def test_transcript_corrupted_line_tolerance(tmp_path: Path):
    sess_dir = tmp_path / "sess_corrupt"
    store = JsonlTranscriptStore(sess_dir)

    await store.append({"type": "step1", "content": "valid step 1"})

    # Manually append a corrupt truncated line
    with open(store.compact_path, "a", encoding="utf-8") as f:
        f.write('{"type": "broken_step", "content": "incomplete jso\n')

    await store.append({"type": "step2", "content": "valid step 2"})

    records = await store.read(compact=True)
    assert len(records) == 2
    contents = [r["content"] for r in records]
    assert "valid step 1" in contents
    assert "valid step 2" in contents


@pytest.mark.asyncio
async def test_transcript_last_compaction(tmp_path: Path):
    sess_dir = tmp_path / "sess_compact"
    store = JsonlTranscriptStore(sess_dir)

    await store.append({"type": "step1", "content": "step 1"})
    await store.append({
        "type": "COMPACTION_SUMMARY",
        "content": "Compacted summary block",
    })
    await store.append({"type": "step2", "content": "step 2"})

    compaction = await store.last_compaction()
    assert compaction is not None
    assert compaction["type"] == "COMPACTION_SUMMARY"
    assert compaction["content"] == "Compacted summary block"


# -----------------------------------------------------------------------------
# 3. Cost Ledger Tests (MDL-06)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cost_ledger_calculation_and_persistence(tmp_path: Path):
    ledger_path = tmp_path / "economics.json"
    ledger = FileCostLedger(ledger_path)

    usage1 = Usage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=500_000,
    )
    # claude-3-5-sonnet: in=3.0, out=15.0, cache=0.30
    # in_cost = 3.0, out_cost = 1.5, cache_cost = 0.15 => total = 4.65
    await ledger.record(
        model="claude-3-5-sonnet-latest",
        provider="anthropic",
        usage=usage1,
        turn_id="turn_1",
    )
    assert usage1.cost_usd == pytest.approx(4.65)

    # Unknown model -> cost is None
    usage_unknown = Usage(input_tokens=100, output_tokens=50)
    await ledger.record(
        model="unlisted-model-xyz",
        provider="custom",
        usage=usage_unknown,
        turn_id="turn_2",
    )
    assert usage_unknown.cost_usd is None

    # Check totals
    totals = await ledger.totals()
    assert totals.input_tokens == 1_000_100
    assert totals.output_tokens == 100_050
    assert totals.cost_usd == pytest.approx(4.65)

    # Verify reload from disk
    reloaded_ledger = FileCostLedger(ledger_path)
    reloaded_totals = await reloaded_ledger.totals()
    assert reloaded_totals.input_tokens == 1_000_100
    assert reloaded_totals.cost_usd == pytest.approx(4.65)


# -----------------------------------------------------------------------------
# 4. Plugin Discovery and Registration
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_store_plugins_discovery(tmp_path: Path):
    bus = EventBus()
    settings = SettingsStore(path=tmp_path / "settings.json")
    secrets = Secrets(path=tmp_path / "secrets.json")
    registry = PluginRegistry(
        workspace=tmp_path,
        settings=settings,
        secrets=secrets,
        bus=bus,
    )
    plugin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=plugin_dir)
    await registry.load_all()

    assert "transcript" in registry.stores
    assert "cost" in registry.stores

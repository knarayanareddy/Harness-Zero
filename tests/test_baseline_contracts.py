"""T02/T03 ledger/SDK checks; does NOT assert that the open defects are repaired."""
from __future__ import annotations

import ast
import inspect
import json
from dataclasses import fields
from pathlib import Path
from typing import get_args

from harness.kernel.commands import Commands
from harness.kernel.gate import PolicyGate
from harness.sdk.errors import ProviderErrorKind
from harness.sdk.types import ImageBlock, ModelRequest

ROOT = Path(__file__).parents[1]


def test_every_review_finding_has_a_real_probe_and_contract_owner():
    ledger = json.loads((ROOT / "docs/testing/review_regressions.json").read_text())
    probes = ast.parse((ROOT / ledger["probe_module"]).read_text())
    functions = {node.name for node in probes.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    rows = ledger["findings"]
    assert {row["id"] for row in rows} == {f"R{i}" for i in range(1, 20)}
    assert len(rows) == 19
    contracts = (ROOT / "docs/architecture/CONTRACTS.md").read_text()
    for row in rows:
        assert row["test"] in functions
        assert f"| {row['contract']} |" in contracts
        assert row["tasks"] and row["tier"] and row["observed"]


def test_supplemental_findings_have_separate_owned_probes():
    ledger = json.loads((ROOT / "docs/testing/review_regressions.json").read_text())
    originals = {row["id"] for row in ledger["findings"]}
    extras = ledger.get("supplemental_findings", [])
    assert len({row["id"] for row in extras}) == len(extras)
    for row in extras:
        assert row["id"] not in originals
        module = ast.parse((ROOT / row["probe_module"]).read_text())
        functions = {node.name for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert row["test"] in functions
        assert row["tasks"] and row["contract"] and row["status"]
        assert (ROOT / row["evidence"]).is_file()


def test_documented_sdk_and_live_command_names_exist():
    assert {f.name for f in fields(ImageBlock)} == {"media_type", "data_b64", "url", "type"}
    params = inspect.signature(ModelRequest).parameters["params"]
    assert params.default is inspect.Parameter.empty
    assert set(get_args(ProviderErrorKind)) == {"auth", "rate_limit", "server", "timeout", "bad_request", "unavailable"}
    for name in ("submit_turn", "cancel_turn", "sessions_create", "sessions_list"):
        assert callable(getattr(Commands, name))
    assert {"call", "cancel_event", "session_id", "log"} <= set(inspect.signature(PolicyGate.execute).parameters)

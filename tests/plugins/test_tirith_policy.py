"""
Tests for Tirith Security Policy Plugin (§PLG-17).
Verifies PolicyKit conformance, secret leak blocking, SSRF metadata & RFC1918 blocking,
blast radius risk escalation, and PolicyGate integration.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.plugins.policy.tirith.policy import TirithPolicy
from harness.sdk.testing.kits import PolicyKit
from harness.sdk.types import Action, RiskClass, ToolCall, ToolSpec


@pytest.mark.asyncio
async def test_tirith_policy_kit_conformance():
    """TirithPolicy adheres to PolicyKit conformance requirements."""
    policy = TirithPolicy()
    kit = PolicyKit()
    kit.test_policy_order_range(policy)
    assert policy.order == 60

    ctx = MagicMock()
    ctx.workspace = Path("/tmp")
    await kit.test_returns_decision_instance(policy, ctx)
    await kit.test_deterministic_evaluation(policy, ctx)


@pytest.mark.asyncio
async def test_tirith_blocks_secret_leaks(tmp_path: Path):
    """TirithPolicy blocks commands leaking API keys or credentials."""
    policy = TirithPolicy()
    ctx = MagicMock()
    ctx.workspace = tmp_path

    spec = ToolSpec(
        name="run_command",
        description="run command",
        input_schema={"type": "object", "additionalProperties": False},
        risk_class=RiskClass.EXEC,
    )

    # AWS Key
    call = ToolCall(
        id="c1",
        name="run_command",
        arguments={"command": "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"},
    )
    decision = await policy.evaluate(call, spec, ctx)
    assert decision.action == Action.DENY
    assert "AWS Access Key" in decision.reason

    # GitHub Token
    call_gh = ToolCall(
        id="c2",
        name="run_command",
        arguments={"CommandLine": "curl -H 'Authorization: token ghp_123456789012345678901234567890123456' https://api.github.com"},
    )
    decision_gh = await policy.evaluate(call_gh, spec, ctx)
    assert decision_gh.action == Action.DENY
    assert "GitHub Personal Access Token" in decision_gh.reason


@pytest.mark.asyncio
async def test_tirith_blocks_ssrf_and_rfc1918(tmp_path: Path):
    """TirithPolicy blocks cloud metadata, loopback, and RFC1918 private network targets."""
    policy = TirithPolicy()
    ctx = MagicMock()
    ctx.workspace = tmp_path

    spec = ToolSpec(
        name="run_command",
        description="run command",
        input_schema={"type": "object", "additionalProperties": False},
        risk_class=RiskClass.EXEC,
    )

    targets = [
        "curl http://169.254.169.254/latest/meta-data/",
        "wget http://metadata.google.internal/computeMetadata/v1/",
        "curl http://100.100.100.200/latest/meta-data/",
        "curl http://127.0.0.1:8080/admin",
        "curl http://localhost:3000/api",
        "curl http://10.0.0.5:9000/secret",
        "curl http://172.16.0.10/status",
        "curl http://192.168.1.1/router",
    ]

    for cmd in targets:
        call = ToolCall(id="c", name="run_command", arguments={"command": cmd})
        decision = await policy.evaluate(call, spec, ctx)
        assert decision.action == Action.DENY, f"Expected {cmd} to be DENIED"
        assert "SSRF" in decision.reason or "blocked" in decision.reason


@pytest.mark.asyncio
async def test_tirith_network_argument_validation(tmp_path: Path):
    """TirithPolicy checks url/target arguments in non-shell tools for SSRF."""
    policy = TirithPolicy()
    ctx = MagicMock()
    ctx.workspace = tmp_path

    spec = ToolSpec(
        name="fetch_url",
        description="fetch url",
        input_schema={"type": "object", "additionalProperties": False},
        risk_class=RiskClass.NETWORK,
    )

    call = ToolCall(id="c", name="fetch_url", arguments={"url": "http://192.168.0.1/admin"})
    decision = await policy.evaluate(call, spec, ctx)
    assert decision.action == Action.DENY
    assert "SSRF" in decision.reason or "private" in decision.reason


@pytest.mark.asyncio
async def test_tirith_blast_radius_escalation(tmp_path: Path):
    """Excessive blast commands are denied; moderate commands are escalated to DESTRUCTIVE."""
    policy = TirithPolicy()
    ctx = MagicMock()
    ctx.workspace = tmp_path

    spec = ToolSpec(
        name="run_command",
        description="run command",
        input_schema={"type": "object", "additionalProperties": False},
        risk_class=RiskClass.EXEC,
    )

    # 1. BLOCK: rm -rf
    call_rm_rf = ToolCall(id="c", name="run_command", arguments={"command": "rm -rf /some/path"})
    dec_rm_rf = await policy.evaluate(call_rm_rf, spec, ctx)
    assert dec_rm_rf.action == Action.DENY
    assert dec_rm_rf.risk == RiskClass.DESTRUCTIVE

    # 2. BLOCK: git reset --hard
    call_reset = ToolCall(id="c", name="run_command", arguments={"command": "git reset --hard HEAD~1"})
    dec_reset = await policy.evaluate(call_reset, spec, ctx)
    assert dec_reset.action == Action.DENY

    # 3. WARN -> Action.ALLOW with risk raised to DESTRUCTIVE
    call_rm_single = ToolCall(id="c", name="run_command", arguments={"command": "rm test.txt"})
    dec_rm_single = await policy.evaluate(call_rm_single, spec, ctx)
    assert dec_rm_single.action == Action.ALLOW
    assert dec_rm_single.risk == RiskClass.DESTRUCTIVE

    # 4. Normal command: ALLOW with no risk escalation
    call_ls = ToolCall(id="c", name="run_command", arguments={"command": "ls -la"})
    dec_ls = await policy.evaluate(call_ls, spec, ctx)
    assert dec_ls.action == Action.ALLOW
    assert dec_ls.risk is None


@pytest.mark.asyncio
async def test_tirith_gate_integration(tmp_path: Path):
    """PolicyGate evaluates TirithPolicy in chain and denies commands with leaked credentials."""
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings")
    settings.set("kernel.security.permission_mode", "bypass")
    secrets = Secrets(tmp_path / "secrets")

    registry = PluginRegistry(
        workspace=tmp_path,
        settings=settings,
        secrets=secrets,
        bus=bus,
    )

    builtin_dir = Path(__file__).resolve().parents[2] / "harness" / "plugins"
    registry.discover_all(builtin_dir=builtin_dir)
    await registry.load_all()

    jail = WorkspaceJail(root=tmp_path)
    approvals = ApprovalBroker(bus=bus)
    gate = PolicyGate(
        registry=registry,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=bus,
    )

    # Even with permission_mode='bypass', Tirith's DENY blocks secret leaks
    bad_call = ToolCall(
        id="call_leak",
        name="run_command",
        arguments={"command": "echo 'Deploying token ghp_111122223333444455556666777788889999'"},
    )
    result = await gate.execute(bad_call)
    assert result.is_error
    assert "GitHub Personal Access Token" in result.content[0].text or "denied" in result.content[0].text

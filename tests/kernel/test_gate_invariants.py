"""
Tests for KRN-06: PolicyGate, WorkspaceJail integration, ApprovalBroker,
and all gate invariants specified in §5.3.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import DiscoveredPlugin, PluginRegistry, TrustTier
from harness.kernel.secrets import Secrets
from harness.kernel.settings import SettingsStore
from harness.sdk.contracts import Hook, Policy
from harness.sdk.manifest import PluginManifest
from harness.sdk.types import (
    SDK_API_VERSION,
    Action,
    Capability,
    Decision,
    HookEvent,
    HookOutcome,
    RiskClass,
    TextBlock,
    ToolAnnotations,
    ToolCall,
    ToolResult,
    ToolSpec,
)


@pytest.fixture
def gate_setup(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings.json")
    secrets = Secrets(tmp_path / "secrets.json")
    jail = WorkspaceJail(workspace)
    approvals = ApprovalBroker(bus=bus, timeout_s=1.0)
    registry = PluginRegistry(
        workspace=workspace,
        settings=settings,
        secrets=secrets,
        bus=bus,
    )
    gate = PolicyGate(
        registry=registry,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=bus,
    )
    return {
        "workspace": workspace,
        "bus": bus,
        "settings": settings,
        "secrets": secrets,
        "jail": jail,
        "approvals": approvals,
        "registry": registry,
        "gate": gate,
    }


@pytest.mark.asyncio
async def test_invariant_unknown_tool_returns_error(gate_setup):
    """Unknown tool name returns ToolResult.error and emits tool.result."""
    gate: PolicyGate = gate_setup["gate"]
    call = ToolCall(id="call_1", name="nonexistent_tool", arguments={})
    res = await gate.execute(call)
    assert res.is_error is True
    assert "unknown tool 'nonexistent_tool'" in res.content[0].text


@pytest.mark.asyncio
async def test_invariant_schema_validation_rejects_invalid_args(gate_setup):
    """Invalid arguments failing JSON Schema validation are rejected before dispatch."""
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    spec = ToolSpec(
        name="calculate",
        description="Math tool",
        input_schema={
            "type": "object",
            "required": ["x"],
            "properties": {"x": {"type": "integer", "minimum": 10}},
        },
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
    )
    handler_called = False

    async def handler(args, ctx):
        nonlocal handler_called
        handler_called = True
        return ToolResult.success("ok")

    registry._register_tool(spec, handler, "calc_plugin")

    # Missing required field
    res_missing = await gate.execute(ToolCall(id="c1", name="calculate", arguments={}))
    assert res_missing.is_error is True
    assert "Missing required" in res_missing.content[0].text
    assert not handler_called

    # Value violates minimum
    res_invalid = await gate.execute(
        ToolCall(id="c2", name="calculate", arguments={"x": 5})
    )
    assert res_invalid.is_error is True
    assert "below minimum" in res_invalid.content[0].text
    assert not handler_called


@pytest.mark.asyncio
async def test_invariant_pre_tool_hook_blocking(gate_setup):
    """PRE_TOOL_USE hook returning block prevents tool dispatch."""
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    spec = ToolSpec(
        name="do_work",
        description="Worker tool",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
    )
    handler_called = False

    async def handler(args, ctx):
        nonlocal handler_called
        handler_called = True
        return ToolResult.success("ok")

    registry._register_tool(spec, handler, "worker_plugin")

    class BlockerHook(Hook):
        name = "safety_guard"
        event = HookEvent.PRE_TOOL_USE

        async def execute(self, call, spec, ctx):
            return HookOutcome(action="block", reason="Safety block triggered")

    registry._register_hook(BlockerHook(), "guard_plugin")

    res = await gate.execute(ToolCall(id="c1", name="do_work", arguments={}))
    assert res.is_error is True
    assert "blocked by hook safety_guard: Safety block triggered" in res.content[0].text
    assert not handler_called


@pytest.mark.asyncio
async def test_invariant_deny_wins_over_allow_and_ask(gate_setup):
    """A DENY decision from any policy strictly wins over ALLOW or ASK."""
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    spec = ToolSpec(
        name="test_tool",
        description="Testing policy combination",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
    )
    handler_called = False

    async def handler(args, ctx):
        nonlocal handler_called
        handler_called = True
        return ToolResult.success("ok")

    registry._register_tool(spec, handler, "p1")

    class AllowPolicy(Policy):
        name = "allow_pol"
        order = 100

        async def evaluate(self, call, spec, ctx):
            return Decision.allow()

    class DenyPolicy(Policy):
        name = "deny_pol"
        order = 200

        async def evaluate(self, call, spec, ctx):
            return Decision(
                action=Action.DENY, reason="Forbidden by DenyPolicy", policy="deny_pol"
            )

    registry._register_policy(AllowPolicy(), "p1")
    registry._register_policy(DenyPolicy(), "p1")

    res = await gate.execute(ToolCall(id="c1", name="test_tool", arguments={}))
    assert res.is_error is True
    assert "Action denied: Forbidden by DenyPolicy" in res.content[0].text
    assert not handler_called


@pytest.mark.asyncio
async def test_invariant_jail_normalisation_and_escape_rejection(gate_setup):
    """
    Workspace jail normalises workspace-path arguments before the handler sees them,
    and blocks path traversal attempts.
    """
    workspace: Path = gate_setup["workspace"]
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    spec = ToolSpec(
        name="read_file",
        description="Reads file in workspace",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "format": "workspace-path"}
            },
        },
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
    )

    received_path = None

    async def handler(args, ctx):
        nonlocal received_path
        received_path = args["file_path"]
        return ToolResult.success("file read ok")

    registry._register_tool(spec, handler, "reader_plugin")

    # 1. Normalisation of relative path to absolute workspace path
    res_valid = await gate.execute(
        ToolCall(id="c1", name="read_file", arguments={"file_path": "sub/test.txt"})
    )
    assert res_valid.is_error is False
    assert received_path == str((workspace / "sub" / "test.txt").resolve())

    # 2. Escape attempt rejected before handler runs
    res_escape = await gate.execute(
        ToolCall(
            id="c2",
            name="read_file",
            arguments={"file_path": "../../etc/passwd"},
        )
    )
    assert res_escape.is_error is True
    assert "Path outside workspace" in res_escape.content[0].text


@pytest.mark.asyncio
async def test_invariant_plan_mode_denies_non_read(gate_setup):
    """Permission mode 'plan' strictly denies any action with risk > READ."""
    settings: SettingsStore = gate_setup["settings"]
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    settings.set("kernel.security.permission_mode", "plan")

    spec_write = ToolSpec(
        name="write_file",
        description="Writes file",
        input_schema={"type": "object"},
        risk_class=RiskClass.WRITE,
        annotations=ToolAnnotations(),
    )
    spec_read = ToolSpec(
        name="view_file",
        description="Views file",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
    )

    async def handler(args, ctx):
        return ToolResult.success("ok")

    registry._register_tool(spec_write, handler, "writer_plugin")
    registry._register_tool(spec_read, handler, "reader_plugin")

    # Write is denied in plan mode
    res_write = await gate.execute(ToolCall(id="c1", name="write_file", arguments={}))
    assert res_write.is_error is True
    assert "denied in plan mode" in res_write.content[0].text

    # Read is allowed in plan mode
    res_read = await gate.execute(ToolCall(id="c2", name="view_file", arguments={}))
    assert res_read.is_error is False


@pytest.mark.asyncio
async def test_invariant_non_interactive_approval_denial(gate_setup):
    """When approval is required in non-interactive session, it is immediately denied."""
    settings: SettingsStore = gate_setup["settings"]
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    # ask mode for write action
    settings.set("kernel.security.permission_mode", "ask")

    spec = ToolSpec(
        name="edit_code",
        description="Modifies code",
        input_schema={"type": "object"},
        risk_class=RiskClass.WRITE,
        annotations=ToolAnnotations(),
    )

    async def handler(args, ctx):
        return ToolResult.success("ok")

    registry._register_tool(spec, handler, "editor_plugin")

    # No interactive subscribers subscribed on bus
    res = await gate.execute(
        ToolCall(id="c1", name="edit_code", arguments={}),
        options={"auto_approve": False},
    )
    assert res.is_error is True
    assert "Approval denied: non-interactive session" in res.content[0].text


@pytest.mark.asyncio
async def test_invariant_approval_approve_and_timeout(gate_setup):
    """Tests interactive approval resolution and timeout behavior."""
    bus: EventBus = gate_setup["bus"]
    approvals: ApprovalBroker = gate_setup["approvals"]
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    # Register an interactive subscriber so broker doesn't deny immediately
    sub = bus.subscribe(interactive=True)

    spec = ToolSpec(
        name="run_command",
        description="Executes command",
        input_schema={"type": "object"},
        risk_class=RiskClass.EXEC,
        annotations=ToolAnnotations(),
    )

    async def handler(args, ctx):
        return ToolResult.success("command output")

    registry._register_tool(spec, handler, "exec_plugin")

    # 1. Interactive approve test
    async def approve_background():
        await asyncio.sleep(0.05)
        pending = approvals.pending()
        assert len(pending) == 1
        approvals.resolve(pending[0]["approval_id"], approved=True, by="test_user")

    task = asyncio.create_task(approve_background())
    res_approved = await gate.execute(
        ToolCall(id="c1", name="run_command", arguments={}),
    )
    await task
    assert res_approved.is_error is False
    assert "command output" in res_approved.content[0].text

    # 2. Timeout test (timeout_s=0.1)
    gate_setup["settings"].set("kernel.security.approval_timeout_s", 1)  # fast timeout
    approvals.timeout_s = 0.1
    res_timeout = await gate.execute(
        ToolCall(id="c2", name="run_command", arguments={}),
    )
    assert res_timeout.is_error is True
    assert "Approval denied: timed out" in res_timeout.content[0].text
    bus.unsubscribe(sub)


@pytest.mark.asyncio
async def test_invariant_bypass_principal_capability(gate_setup):
    """approval:bypass is honoured when held by session principal."""
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    spec = ToolSpec(
        name="dangerous_tool",
        description="Dangerous action",
        input_schema={"type": "object"},
        risk_class=RiskClass.DESTRUCTIVE,
        annotations=ToolAnnotations(),
    )

    async def handler(args, ctx):
        return ToolResult.success("executed dangerous action")

    registry._register_tool(spec, handler, "danger_plugin")

    # Executing with principal_capabilities containing APPROVAL_BYPASS
    res = await gate.execute(
        ToolCall(id="c1", name="dangerous_tool", arguments={}),
        principal_capabilities=frozenset([Capability.APPROVAL_BYPASS]),
    )
    assert res.is_error is False
    assert "executed dangerous action" in res.content[0].text


@pytest.mark.asyncio
async def test_invariant_tool_context_capability_gating(gate_setup):
    """ToolContext.executor raises CapabilityDenied when plugin lacks EXEC capability."""
    settings: SettingsStore = gate_setup["settings"]
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]
    # This invariant concerns capability gating, not the separate ask-before-unconfined sandbox policy.
    settings.set("kernel.sandbox.mode", "off")

    # Register a plugin with only FS_READ capability
    manifest = PluginManifest(
        name="no-exec-plugin",
        version="0.1.0",
        api_version=SDK_API_VERSION,
        summary="No exec",
        entry="dummy",
        capabilities=["fs:read"],
    )
    dp = DiscoveredPlugin(manifest, TrustTier.LOCAL, "origin")
    registry.register_discovered(dp)

    class NoExecPlugin:
        async def setup(self, ctx):
            pass

        async def teardown(self):
            pass

    registry._instantiate_plugin = lambda dp: NoExecPlugin()
    await registry.load_all()

    # Tool spec requests EXEC
    spec = ToolSpec(
        name="need_exec",
        description="Tool needing exec",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
        capabilities=frozenset([Capability.EXEC]),
    )

    async def handler(args, ctx):
        # Accessing executor should raise CapabilityDenied
        _ = ctx.executor
        return ToolResult.success("ok")

    # Registering directly into registry tools for this plugin
    registry.tools["need_exec"] = (spec, handler, "no-exec-plugin")

    res = await gate.execute(ToolCall(id="c1", name="need_exec", arguments={}))
    assert res.is_error is True
    assert "CapabilityDenied: Capability 'exec' denied" in res.content[0].text


@pytest.mark.asyncio
async def test_invariant_output_secret_sanitization_and_truncation(gate_setup):
    """
    Secrets appearing in tool output are redacted and results exceeding
    limit are truncated with full text preserved in metadata.
    """
    secrets: Secrets = gate_setup["secrets"]
    settings: SettingsStore = gate_setup["settings"]
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    # Configure custom secret and small limit (>= 1024 minimum)
    secrets.set("anthropic", "sk-ant-customsecret999")
    settings.set("kernel.limits.tool_result_bytes", 1024)

    spec = ToolSpec(
        name="leak_secrets",
        description="Leaky tool",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
    )

    async def handler(args, ctx):
        # Emits both regex pattern (AWS key) and configured exact secret
        long_padding = "A" * 1500
        text = f"AWS: AKIA1234567890ABCDEF and ANTHROPIC: sk-ant-customsecret999 {long_padding}"
        return ToolResult(content=[TextBlock(text=text)])

    registry._register_tool(spec, handler, "leaker_plugin")

    res = await gate.execute(ToolCall(id="c1", name="leak_secrets", arguments={}))

    text = res.content[0].text
    # Check secrets redacted
    assert "AKIA1234567890ABCDEF" not in text
    assert "[REDACTED AWS KEY]" in text
    assert "sk-ant-customsecret999" not in text
    assert "[REDACTED SECRET]" in text

    # Check truncation
    assert res.metadata.get("truncated") is True
    assert "... [TRUNCATED DUE TO SIZE LIMIT]" in text
    assert "full_content" in res.metadata
    assert len(res.metadata["full_content"]) > 200


@pytest.mark.asyncio
async def test_invariant_hooks_and_policies_cannot_crash_turn(gate_setup):
    """Faulty policies, hooks, or handlers cannot raise exceptions into the turn."""
    registry: PluginRegistry = gate_setup["registry"]
    gate: PolicyGate = gate_setup["gate"]

    spec = ToolSpec(
        name="crashing_tool",
        description="Tool that raises error",
        input_schema={"type": "object"},
        risk_class=RiskClass.READ,
        annotations=ToolAnnotations(),
    )

    async def handler(args, ctx):
        raise ZeroDivisionError("division by zero in tool handler")

    registry._register_tool(spec, handler, "crasher_plugin")

    class CrashingPolicy(Policy):
        name = "bad_policy"
        order = 50

        async def evaluate(self, call, spec, ctx):
            raise ValueError("policy blew up")

    registry._register_policy(CrashingPolicy(), "p1")

    # Gate must NOT raise; it must return ToolResult.error
    res = await gate.execute(ToolCall(id="c1", name="crashing_tool", arguments={}))
    assert res.is_error is True
    assert "Policy error in bad_policy: policy blew up" in res.content[0].text

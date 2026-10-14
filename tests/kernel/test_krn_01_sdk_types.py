from pathlib import Path

import pytest

from harness.sdk import (
    RISK_ORDER,
    SDK_API_VERSION,
    Action,
    ApprovalDenied,
    Capability,
    CapabilityDenied,
    ContextBlock,
    Decision,
    ExecRequest,
    ExecResult,
    HarnessError,
    HookEvent,
    HookOutcome,
    ImageBlock,
    IncompatiblePlugin,
    Message,
    ModelInfo,
    ModelParams,
    PathOutsideWorkspace,
    PluginLoadError,
    PluginManifest,
    ProviderCaps,
    ProviderError,
    RiskClass,
    Role,
    SpendCapExceeded,
    Stability,
    TextBlock,
    ThinkingBlock,
    ToolAnnotations,
    ToolCall,
    ToolInputError,
    ToolResult,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    TurnResult,
    Usage,
)


def test_sdk_api_version():
    assert SDK_API_VERSION == "1"


def test_error_hierarchy():
    # Base error
    err = HarnessError("base error")
    assert isinstance(err, Exception)

    # ProviderError
    pe = ProviderError(
        provider="anthropic",
        model="claude-3-7-sonnet",
        status_code=429,
        retryable=True,
        kind="rate_limit",
        message="Too many requests",
    )
    assert isinstance(pe, HarnessError)
    assert pe.provider == "anthropic"
    assert pe.model == "claude-3-7-sonnet"
    assert pe.status_code == 429
    assert pe.retryable is True
    assert pe.kind == "rate_limit"
    assert "Too many requests" in str(pe)
    assert pe.to_dict()["kind"] == "rate_limit"

    # PathOutsideWorkspace
    p_err = PathOutsideWorkspace("/etc/shadow")
    assert isinstance(p_err, HarnessError)
    assert isinstance(p_err, PermissionError)

    # CapabilityDenied
    c_err = CapabilityDenied(capability="secrets", plugin="my-plugin", message="No secrets access")
    assert isinstance(c_err, HarnessError)
    assert isinstance(c_err, PermissionError)
    assert c_err.capability == "secrets"
    assert "my-plugin" in str(c_err)

    # ToolInputError
    t_err = ToolInputError("Invalid args")
    assert isinstance(t_err, HarnessError)
    assert isinstance(t_err, ValueError)

    # ApprovalDenied
    a_err = ApprovalDenied("User denied")
    assert isinstance(a_err, HarnessError)
    assert isinstance(a_err, PermissionError)

    # SpendCapExceeded
    s_err = SpendCapExceeded("Budget exceeded")
    assert isinstance(s_err, HarnessError)
    assert isinstance(s_err, RuntimeError)

    # PluginLoadError & IncompatiblePlugin
    pl_err = PluginLoadError("Bad TOML")
    assert isinstance(pl_err, HarnessError)
    inc_err = IncompatiblePlugin("Version mismatch")
    assert isinstance(inc_err, PluginLoadError)
    assert isinstance(inc_err, HarnessError)


def test_content_blocks_and_message_roundtrip():
    tb = TextBlock(text="Hello world")
    ib = ImageBlock(media_type="image/png", url="http://example.com/pic.png")
    thb = ThinkingBlock(text="Deep reasoning...", signature="sig123")
    tub = ToolUseBlock(id="call_1", name="view_file", input={"path": "main.py"})
    trb = ToolResultBlock(tool_use_id="call_1", content=[TextBlock(text="file contents")], is_error=False)

    msg = Message(
        role=Role.USER,
        content=[tb, ib, thb, tub, trb],
        name="tester",
        metadata={"step_index": 1},
    )

    assert msg.text() == "Hello world"
    d = msg.to_dict()
    roundtrip = Message.from_dict(d)

    assert roundtrip.role == Role.USER
    assert roundtrip.name == "tester"
    assert roundtrip.metadata["step_index"] == 1
    assert len(roundtrip.content) == 5
    assert isinstance(roundtrip.content[0], TextBlock)
    assert roundtrip.content[0].text == "Hello world"
    assert isinstance(roundtrip.content[1], ImageBlock)
    assert roundtrip.content[1].url == "http://example.com/pic.png"
    assert isinstance(roundtrip.content[2], ThinkingBlock)
    assert roundtrip.content[2].signature == "sig123"
    assert isinstance(roundtrip.content[3], ToolUseBlock)
    assert roundtrip.content[3].name == "view_file"
    assert isinstance(roundtrip.content[4], ToolResultBlock)
    assert roundtrip.content[4].tool_use_id == "call_1"


def test_usage_addition_and_roundtrip():
    u1 = Usage(input_tokens=100, output_tokens=50, cache_read_tokens=20, cost_usd=0.005)
    u2 = Usage(input_tokens=200, output_tokens=80, cache_write_tokens=10, cost_usd=0.010)

    u3 = u1 + u2
    assert u3.input_tokens == 300
    assert u3.output_tokens == 130
    assert u3.cache_read_tokens == 20
    assert u3.cache_write_tokens == 10
    assert u3.cost_usd == pytest.approx(0.015)

    d = u3.to_dict()
    rt = Usage.from_dict(d)
    assert rt == u3


def test_tool_spec_and_result_roundtrip():
    ann = ToolAnnotations(read_only=True, open_world=False, title="File Reader")
    spec = ToolSpec(
        name="view_file",
        description="Inspects file content",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        risk_class=RiskClass.READ,
        capabilities=frozenset(["fs:read"]),
        annotations=ann,
        namespace="fs",
        timeout_s=30.0,
    )
    d = spec.to_dict()
    rt_spec = ToolSpec.from_dict(d)
    assert rt_spec.name == "view_file"
    assert rt_spec.risk_class == RiskClass.READ
    assert "fs:read" in rt_spec.capabilities
    assert rt_spec.annotations.read_only is True

    # ToolResult classmethods
    tr = ToolResult.text("Sample output", task_id="t123")
    assert tr.output == "Sample output"
    assert tr.metadata["task_id"] == "t123"
    assert not tr.is_error

    tr_err = ToolResult.error("Failure", code=500)
    assert tr_err.is_error is True
    assert tr_err.output == "Failure"
    assert tr_err.metadata["code"] == 500


def test_decision_and_risk_order():
    assert RISK_ORDER == [
        RiskClass.READ,
        RiskClass.WRITE,
        RiskClass.EXEC,
        RiskClass.NETWORK,
        RiskClass.DESTRUCTIVE,
    ]

    d_allow = Decision.allow("Safe operation")
    assert d_allow.action == Action.ALLOW
    assert d_allow.reason == "Safe operation"

    d_ask = Decision.ask("Destructive mutation", risk=RiskClass.DESTRUCTIVE)
    assert d_ask.action == Action.ASK
    assert d_ask.risk == RiskClass.DESTRUCTIVE

    d_deny = Decision.deny("Outside workspace", risk=RiskClass.DESTRUCTIVE)
    assert d_deny.action == Action.DENY
    rt = Decision.from_dict(d_deny.to_dict())
    assert rt.action == Action.DENY
    assert rt.risk == RiskClass.DESTRUCTIVE


def test_context_and_model_types_roundtrip():
    cb = ContextBlock(id="identity", text="Agent prompt", stability=Stability.STATIC, priority=10, source="core")
    d_cb = cb.to_dict()
    assert ContextBlock.from_dict(d_cb) == cb

    caps = ProviderCaps(native_tools=True, streaming=True, vision=True, cache_hints=True, thinking=True)
    mi = ModelInfo(id="gpt-4o", display_name="GPT-4o", caps=caps)
    d_mi = mi.to_dict()
    rt_mi = ModelInfo.from_dict(d_mi)
    assert rt_mi.id == "gpt-4o"
    assert rt_mi.caps is not None and rt_mi.caps.native_tools is True

    mp = ModelParams(temperature=0.7, max_tokens=2048)
    assert ModelParams.from_dict(mp.to_dict()) == mp

    tc = ToolCall(id="c1", name="read", arguments={"f": "a.txt"})
    assert ToolCall.from_dict(tc.to_dict()) == tc


def test_exec_and_hook_types_roundtrip(tmp_path: Path):
    req = ExecRequest(command="ls", cwd=tmp_path, interactive=True)
    d_req = req.to_dict()
    rt_req = ExecRequest.from_dict(d_req)
    assert rt_req.command == "ls"
    assert rt_req.interactive is True

    res = ExecResult(exit_code=0, stdout="out", stderr="", truncated=False, duration_ms=15)
    d_res = res.to_dict()
    assert ExecResult.from_dict(d_res) == res

    assert HookEvent.SESSION_START == "session_start"
    assert Capability.FS_READ == "fs:read"

    outcome = HookOutcome(action="continue", reason="allowed", additional_context="ctx")
    d_out = outcome.to_dict()
    assert HookOutcome.from_dict(d_out) == outcome


def test_turn_result_roundtrip():
    tr = TurnResult(
        output="Result text",
        thinking="Thoughts...",
        usage=Usage(input_tokens=50, output_tokens=25),
        tool_calls=2,
        stop_reason="end_turn",
        error=None,
    )
    d = tr.to_dict()
    rt = TurnResult.from_dict(d)
    assert rt.output == "Result text"
    assert rt.usage.input_tokens == 50
    assert rt.tool_calls == 2


def test_plugin_manifest_validation(tmp_path: Path):
    # 1. Valid manifest
    valid_toml = """
[plugin]
name = "repo-map"
version = "0.1.0"
api_version = "1"
summary = "Aider-style repository map"
entry = "harness.plugins.context.repo_map:RepoMapPlugin"
provides = ["context"]
capabilities = ["fs:read"]
requires = []
platforms = ["darwin", "linux"]

[settings]
schema = "settings.schema.json"

[compat]
harness = ">=0.2,<0.3"
python = ">=3.11"
"""
    manifest_file = tmp_path / "harness_plugin.toml"
    manifest_file.write_text(valid_toml, encoding="utf-8")

    manifest = PluginManifest.load(tmp_path)
    assert manifest.name == "repo-map"
    assert manifest.version == "0.1.0"
    assert manifest.api_version == "1"
    assert manifest.provides == ["context"]
    assert manifest.capabilities == ["fs:read"]
    assert manifest.settings_schema == "settings.schema.json"

    # 2. Invalid name (uppercase / invalid chars)
    bad_name_toml = valid_toml.replace('name = "repo-map"', 'name = "Repo_Map!"')
    manifest_file.write_text(bad_name_toml, encoding="utf-8")
    with pytest.raises(PluginLoadError) as exc:
        PluginManifest.load(tmp_path)
    assert "Invalid plugin name" in str(exc.value)

    # 3. Incompatible api_version
    bad_api_toml = valid_toml.replace('api_version = "1"', 'api_version = "99"')
    manifest_file.write_text(bad_api_toml, encoding="utf-8")
    with pytest.raises(IncompatiblePlugin) as exc:
        PluginManifest.load(tmp_path)
    assert "requires api_version '99'" in str(exc.value)

    # 4. Unknown capability
    bad_cap_toml = valid_toml.replace('capabilities = ["fs:read"]', 'capabilities = ["kernel:root_access"]')
    manifest_file.write_text(bad_cap_toml, encoding="utf-8")
    with pytest.raises(PluginLoadError) as exc:
        PluginManifest.load(tmp_path)
    assert "declares unknown capability" in str(exc.value)

    # 5. Unknown component type in provides
    bad_prov_toml = valid_toml.replace('provides = ["context"]', 'provides = ["nuclear_reactor"]')
    manifest_file.write_text(bad_prov_toml, encoding="utf-8")
    with pytest.raises(PluginLoadError) as exc:
        PluginManifest.load(tmp_path)
    assert "declares invalid component type in 'provides'" in str(exc.value)

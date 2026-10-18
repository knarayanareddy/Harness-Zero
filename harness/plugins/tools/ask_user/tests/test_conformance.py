"""Conformance checks for the built-in ask_user tool."""
from __future__ import annotations

from harness.plugins.tools.ask_user.tools import ASK_USER_SPEC, handle_ask_user
from harness.sdk.testing import ToolKit


def test_ask_user_tool_contract() -> None:
    kit = ToolKit()
    kit.test_input_schema_valid_and_closed(ASK_USER_SPEC)
    kit.test_declared_risk_class_bound(ASK_USER_SPEC)
    kit.test_no_direct_subprocess_import(handle_ask_user)

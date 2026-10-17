"""Plugin-local conformance for the deliverables tool."""
from __future__ import annotations

from typing import Any

import pytest

from harness.plugins.tools.deliverables.plugin import DeliverablesPlugin
from harness.plugins.tools.deliverables.tools import PRESENT_SPEC, handle_present
from harness.sdk.testing.kits import ToolKit


def test_present_tool_contract() -> None:
    kit = ToolKit()
    kit.test_input_schema_valid_and_closed(PRESENT_SPEC)
    kit.test_declared_risk_class_bound(PRESENT_SPEC)
    assert PRESENT_SPEC.risk_class.value == "read"
    assert PRESENT_SPEC.capabilities == frozenset({"fs:read"})


@pytest.mark.asyncio
async def test_plugin_registers_present_tool() -> None:
    registrations: list[tuple[Any, Any]] = []

    class Context:
        def register_tool(self, spec, handler) -> None:
            registrations.append((spec, handler))

    await DeliverablesPlugin().setup(Context())
    assert registrations == [(PRESENT_SPEC, handle_present)]

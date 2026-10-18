"""Conformance and handler tests for the ask_user plugin."""
from __future__ import annotations

from typing import Any

import pytest

from harness.plugins.tools.ask_user.tools import ASK_USER_SPEC, handle_ask_user
from harness.sdk.testing import ToolKit
from harness.sdk.types import RiskClass

QUESTION = {
    "id": "format",
    "question": "Which format should I use?",
    "options": [
        {"id": "md", "label": "Markdown"},
        {"id": "html", "label": "HTML"},
    ],
}


class AnsweringContext:
    def remaining_time(self) -> float:
        return 30.0

    async def ask_user(self, questions: list[dict[str, Any]], timeout_s: float | None = None):
        assert questions[0]["allow_custom"] is True
        assert timeout_s == 30.0
        return {"format": {"option_id": "md", "custom_text": ""}}


def test_ask_user_spec_is_read_only_and_conformant() -> None:
    kit = ToolKit()
    assert ASK_USER_SPEC.risk_class == RiskClass.READ
    assert ASK_USER_SPEC.timeout_s == 1800.0
    kit.test_input_schema_valid_and_closed(ASK_USER_SPEC)
    kit.test_declared_risk_class_bound(ASK_USER_SPEC)
    kit.test_no_direct_subprocess_import(handle_ask_user)


@pytest.mark.asyncio
async def test_handler_formats_the_answer_for_the_model() -> None:
    result = await handle_ask_user({"questions": [QUESTION]}, AnsweringContext())
    assert not result.is_error
    assert result.metadata["answers"]["format"]["option_id"] == "md"
    assert result.content[0].text == "User answered:\nWhich format should I use? → Markdown"


@pytest.mark.asyncio
async def test_handler_returns_actionable_error_without_an_answer() -> None:
    class UnavailableContext(AnsweringContext):
        async def ask_user(self, questions, timeout_s=None):
            return None

    result = await handle_ask_user({"questions": [QUESTION]}, UnavailableContext())
    assert result.is_error
    assert "ask_user unavailable: no interactive client connected" in result.content[0].text

"""Structured user-question tool and its public ToolSpec."""
from __future__ import annotations

from typing import Any

from harness.sdk.context import ToolContext
from harness.sdk.types import RiskClass, TextBlock, ToolResult, ToolSpec

ASK_USER_SPEC = ToolSpec(
    name="ask_user",
    description=(
        "Ask the user up to 4 short multiple-choice questions when a decision materially changes what you will build. "
        "Prefer sensible defaults over asking; never ask what you can find out from the workspace."
    ),
    input_schema={
        "type": "object",
        "required": ["questions"],
        "additionalProperties": False,
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "required": ["id", "question", "options"],
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "question": {"type": "string", "maxLength": 300},
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "required": ["id", "label"],
                                "additionalProperties": False,
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string", "maxLength": 80},
                                    "description": {"type": "string", "maxLength": 200},
                                },
                            },
                        },
                        "allow_custom": {"type": "boolean", "default": True},
                    },
                },
            },
        },
    },
    risk_class=RiskClass.READ,
    timeout_s=1800.0,
)


def _format_answer(question: dict[str, Any], answer: Any) -> str:
    """Turn the stable option id stored in the log into user-readable model context."""
    if isinstance(answer, str):
        option_id, custom_text = answer, ""
    elif isinstance(answer, dict):
        option_id = answer.get("option_id", "")
        custom_text = str(answer.get("custom_text", "") or "").strip()
    else:
        return str(answer)

    if option_id == "custom":
        return custom_text
    option = next((item for item in question["options"] if item.get("id") == option_id), None)
    label = str(option.get("label", option_id)) if option else str(option_id)
    return f"{label} — {custom_text}" if custom_text else label


async def handle_ask_user(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Ask the active client and return the selected choices as model-visible tool output."""
    questions = [
        {**question, "allow_custom": question.get("allow_custom", True)}
        for question in args["questions"]
    ]
    try:
        timeout_s = min(1800.0, max(0.0, float(ctx.remaining_time())))
        answer = await ctx.ask_user(questions=questions, timeout_s=timeout_s)
    except ValueError as err:
        return ToolResult.error(str(err))

    if answer is None:
        return ToolResult.error(
            "ask_user unavailable: no interactive client connected (or no answer in time). "
            "Proceed with your best assumption and state it."
        )

    lines = [
        f"{question['question']} → {_format_answer(question, answer[question['id']])}"
        for question in questions
    ]
    return ToolResult(
        content=[TextBlock(text="User answered:\n" + "\n".join(lines))],
        metadata={"answers": answer},
    )

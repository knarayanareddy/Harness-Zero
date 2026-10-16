"""
Acceptance test for the agent loop memory, transcript persistence, and roundtrip context.
Fulfills review/CODE_REVIEW_v0.2.0.md §4.1 and §9.1.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ModelInfo,
    Role,
    TextBlock,
    TextDelta,
    ToolCallEnd,
    ToolResultBlock,
    ToolUseBlock,
    TurnResult,
    Usage,
    UsageEvent,
)


@pytest.mark.asyncio
async def test_agent_loop_turn_roundtrip_and_persistence(tmp_path: Path):
    """
    Acceptance criteria from review/CODE_REVIEW_v0.2.0.md §4.1:
    1. Turn 1 with tool call:
       - Iteration 1 sends prompt once.
       - Iteration 2 receives assistant[tool_use] -> user[tool_result(file content)].
       - Prompt appears exactly once (no duplicate appended after tool results).
    2. Turn 2 in the same session:
       - Iteration 1 receives Turn 1's full history followed by Turn 2 prompt.
    3. Persistence:
       - transcript.jsonl exists under <workspace>/.harness/sessions/<session_id>/.
       - Reopening session from disk loads the full history.
       - sessions.get returns records.
    """
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    conf = tmp_path / ".harness"
    conf.mkdir(parents=True, exist_ok=True)

    # Seed target file in workspace
    app_file = ws / "app.py"
    app_file.write_text("print('hello harness roundtrip')\n", encoding="utf-8")

    runtime = await bootstrap_kernel(workspace=ws, config_dir=conf)

    # Configure ScriptedProvider with 3 responses across 2 turns
    provider = ScriptedProvider(
        models=[
            ModelInfo(
                id="gpt-4o",
                display_name="GPT-4o",
                context_tokens=128000,
                input_usd_per_mtok=2.5,
                output_usd_per_mtok=10.0,
            )
        ],
    )

    # Turn 1, Iteration 1 response: calls view_file("app.py")
    provider.add_response([
        TextDelta(text="Reading app.py..."),
        ToolCallEnd(id="call_read_app", name="view_file", arguments={"file_path": "app.py"}),
        UsageEvent(usage=Usage(input_tokens=100, output_tokens=30)),
        Done(stop_reason="tool_use"),
    ])

    # Turn 1, Iteration 2 response: concludes Turn 1
    provider.add_response([
        TextDelta(text="app.py prints hello harness roundtrip."),
        UsageEvent(usage=Usage(input_tokens=180, output_tokens=25)),
        Done(stop_reason="end_turn"),
    ])

    # Turn 2, Iteration 1 response: answers follow-up
    provider.add_response([
        TextDelta(text="Yes, it prints hello harness roundtrip!"),
        UsageEvent(usage=Usage(input_tokens=250, output_tokens=20)),
        Done(stop_reason="end_turn"),
    ])

    runtime.registry.register_provider(provider, plugin_name="test_scripted")

    # 1. Create a session
    session = await runtime.session_manager.create_session()
    session_id = session.session_id

    # 2. Execute Turn 1
    result1 = await runtime.runner.execute_turn(
        session=session.info,
        prompt="read app.py",
        options={"provider": "scripted", "model": "gpt-4o"},
    )
    assert isinstance(result1, TurnResult)
    assert "hello harness roundtrip" in result1.output

    # Assert model requests for Turn 1
    assert len(provider.requests) == 2

    # Request 0 (Turn 1, iter 1): only initial prompt
    req0 = provider.requests[0]
    user_prompts_req0 = [m for m in req0.messages if m.role == Role.USER]
    assert len(user_prompts_req0) == 1
    assert user_prompts_req0[0].text() == "read app.py"

    # Request 1 (Turn 1, iter 2): assistant tool use + user tool result
    req1 = provider.requests[1]
    # Check that "read app.py" appears EXACTLY ONCE
    user_text_messages = [m for m in req1.messages if m.role == Role.USER and m.text() == "read app.py"]
    assert len(user_text_messages) == 1, f"Expected prompt once, got {len(user_text_messages)}"

    # The end of req1.messages must be assistant[tool_use] -> user[tool_result]
    assert len(req1.messages) >= 3
    msg_asst = req1.messages[-2]
    msg_tool_res = req1.messages[-1]

    assert msg_asst.role == Role.ASSISTANT
    tool_uses = [b for b in msg_asst.content if isinstance(b, ToolUseBlock)]
    assert len(tool_uses) == 1
    assert tool_uses[0].name == "view_file"
    assert tool_uses[0].id == "call_read_app"

    assert msg_tool_res.role == Role.USER
    tool_results = [b for b in msg_tool_res.content if isinstance(b, ToolResultBlock)]
    assert len(tool_results) == 1
    assert tool_results[0].tool_use_id == "call_read_app"
    assert not tool_results[0].is_error
    result_text = "".join(b.text for b in tool_results[0].content if isinstance(b, TextBlock))
    assert "hello harness roundtrip" in result_text

    # 3. Execute Turn 2 in the same session
    result2 = await runtime.runner.execute_turn(
        session=session.info,
        prompt="what was in app.py?",
        options={"provider": "scripted", "model": "gpt-4o"},
    )
    assert isinstance(result2, TurnResult)
    assert len(provider.requests) == 3

    # Request 2 (Turn 2, iter 1): must contain all Turn 1 history + Turn 2 prompt
    req2 = provider.requests[2]
    assert len(req2.messages) >= 5

    # Verify message sequence in Turn 2
    assert req2.messages[0].role == Role.USER and req2.messages[0].text() == "read app.py"
    assert req2.messages[1].role == Role.ASSISTANT and any(isinstance(b, ToolUseBlock) for b in req2.messages[1].content)
    assert req2.messages[2].role == Role.USER and any(isinstance(b, ToolResultBlock) for b in req2.messages[2].content)
    assert req2.messages[3].role == Role.ASSISTANT and "app.py prints" in req2.messages[3].text()
    assert req2.messages[4].role == Role.USER and req2.messages[4].text() == "what was in app.py?"

    # Prompt "what was in app.py?" appears exactly once
    t2_prompts = [m for m in req2.messages if m.role == Role.USER and m.text() == "what was in app.py?"]
    assert len(t2_prompts) == 1

    # 4. Verify disk persistence under session dir
    session_dir = ws / ".harness" / "sessions" / session_id
    assert session_dir.is_dir()
    transcript_file = session_dir / "transcript.jsonl"
    full_transcript_file = session_dir / "transcript_full.jsonl"
    assert transcript_file.exists(), f"Missing {transcript_file}"
    assert full_transcript_file.exists(), f"Missing {full_transcript_file}"

    transcript_lines = transcript_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(transcript_lines) >= 5, f"Expected at least 5 records, got {len(transcript_lines)}"

    # 5. Verify sessions.get command
    session_info = await runtime.commands.dispatch("sessions.get", {"session_id": session_id})
    assert session_info["session_id"] == session_id
    assert len(session_info.get("records", [])) >= 5

    # 6. Reopen session from disk
    reopened_session = runtime.session_manager.get_session(session_id)
    history = await reopened_session.get_history()
    assert len(history) >= 5
    # Verify what the log holds. Since WS-04 this is asserted by record *kind*, not role: a turn marker
    # and a system notice are both `system`-role, and a tool result is `user`-role with empty content, so
    # roles could not tell the shapes apart. The conversation projection is what a client renders; the
    # markers are what makes a reload able to say how each turn ended.
    from harness.kernel.transcript import MESSAGE_KINDS

    kinds = [r["kind"] for r in history]
    assert kinds == [
        "user.message",
        "assistant.message",  # the model asks for a tool
        "tool.decision",  # ... the gate records what it decided ...
        "tool.result",  # ... and what came back
        "assistant.message",  # ... and answers with its output
        "turn.completed",
        "user.message",
        "assistant.message",
        "turn.completed",
    ], kinds
    assert [k for k in kinds if k in MESSAGE_KINDS] == [
        kinds[0], kinds[1], kinds[3], kinds[4], kinds[6], kinds[7]
    ]
    assert kinds.count("turn.completed") == 2, f"one marker per finished turn: {kinds}"

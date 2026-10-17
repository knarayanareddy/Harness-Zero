"""WS-08 fail-first integration coverage for structured human questions."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from harness.frontends.web.server import HarnessWebServer
from harness.kernel.bootstrap import bootstrap_kernel
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import Done, ModelInfo, TextDelta, ToolCallEnd

MODEL = ModelInfo(
    id="ask-user-scripted",
    display_name="Ask User Scripted",
    context_tokens=128_000,
    input_usd_per_mtok=0.0,
    output_usd_per_mtok=0.0,
)
QUESTION = {
    "id": "format",
    "question": "Which format should I use?",
    "options": [
        {"id": "md", "label": "Markdown", "description": "Readable plain text"},
        {"id": "html", "label": "HTML", "description": "Rich browser layout"},
    ],
    "allow_custom": True,
}


def _question_response() -> list[Any]:
    return [
        ToolCallEnd(
            id="call_question",
            name="ask_user",
            arguments={"questions": [QUESTION]},
        ),
        Done(stop_reason="tool_use"),
    ]


async def _boot(tmp_path: Path, responses: list[list[Any]]):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace,
        config_dir=config,
        overrides={
            "kernel.security.permission_mode": "allow",
            "kernel.provider": "scripted",
            "kernel.model": MODEL.id,
        },
        resume_session=False,
    )
    provider = ScriptedProvider(models=[MODEL])
    for response in responses:
        provider.add_response(response)
    runtime.registry.register_provider(provider, plugin_name="test_ask_user")
    session = await runtime.session_manager.create_session()
    return runtime, provider, session


async def _wait_for_question(subscriber, timeout: float = 2.0) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        while True:
            event = await subscriber.get()
            if event.type == "question.asked":
                return event.to_dict()


@pytest.mark.asyncio
async def test_question_blocks_until_answer_and_returns_it(tmp_path: Path) -> None:
    """The turn stays blocked until the web command answers; the next model request sees that answer."""
    runtime, provider, session = await _boot(
        tmp_path,
        [_question_response(), [TextDelta(text="I will use Markdown."), Done(stop_reason="end_turn")]],
    )
    server = HarnessWebServer(
        commands=runtime.commands,
        bus=runtime.bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=runtime.commands.workspace,
    )
    subscriber = runtime.bus.subscribe(interactive=True)
    await server.start()
    try:
        turn = asyncio.create_task(
            runtime.commands.submit_turn(
                session_id=session.session_id,
                prompt="Ask me which format to use.",
                options={"provider": "scripted", "model": MODEL.id},
            )
        )
        asked = await _wait_for_question(subscriber)
        assert not turn.done(), "ask_user must block the tool call until the user answers"
        interaction_id = asked["data"]["interaction_id"]
        headers = {"Authorization": f"Bearer {server.auth_token}"}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{server.port}", headers=headers) as client:
            response = await client.post(
                "/commands/question.answer",
                json={
                    "interaction_id": interaction_id,
                    "answers": {"format": {"option_id": "md", "custom_text": ""}},
                },
            )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["status"] == "answered"
        result = await asyncio.wait_for(turn, timeout=2.0)
        assert result.stop_reason == "end_turn"
        assert "Markdown" in "\n".join(
            str(message) for request in provider.requests[1:] for message in request.messages
        )
    finally:
        if not turn.done():
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        runtime.bus.unsubscribe(subscriber)
        await server.stop()
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_headless_returns_error_within_1s(tmp_path: Path) -> None:
    """No interactive subscriber means the question tool returns promptly instead of hanging."""
    runtime, _provider, session = await _boot(
        tmp_path,
        [_question_response(), [TextDelta(text="I will assume Markdown."), Done(stop_reason="end_turn")]],
    )
    try:
        started = time.monotonic()
        result = await asyncio.wait_for(
            runtime.commands.submit_turn(
                session_id=session.session_id,
                prompt="Ask a question in headless mode.",
                options={"provider": "scripted", "model": MODEL.id},
            ),
            timeout=1.0,
        )
        assert time.monotonic() - started < 1.0
        assert result.stop_reason == "end_turn"
        records = await session.get_history()
        assert any(
            "ask_user unavailable: no interactive client connected" in str(record.get("tool_results", []))
            for record in records
        )
    finally:
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_question_and_answer_are_records(tmp_path: Path) -> None:
    """Both sides are durable and the public session reload returns them in order."""
    runtime, _provider, session = await _boot(
        tmp_path,
        [_question_response(), [TextDelta(text="Thanks."), Done(stop_reason="end_turn")]],
    )
    subscriber = runtime.bus.subscribe(interactive=True)
    try:
        turn = asyncio.create_task(
            runtime.commands.submit_turn(
                session_id=session.session_id,
                prompt="Ask and log a question.",
                options={"provider": "scripted", "model": MODEL.id},
            )
        )
        asked = await _wait_for_question(subscriber)
        interaction_id = asked["data"]["interaction_id"]
        legacy_pending = await runtime.commands.dispatch("approvals.list", {})
        assert any(item.get("interaction_id") == interaction_id for item in legacy_pending)
        # The old form.submit endpoint remains an alias when the form id names a pending question.
        answer = await runtime.commands.form_submit(
            form_id=interaction_id,
            data={"format": {"option_id": "html", "custom_text": ""}},
        )
        assert answer["status"] == "answered"
        assert answer["form_id"] == interaction_id
        await asyncio.wait_for(turn, timeout=2.0)

        fetched = await runtime.commands.sessions_get(session_id=session.session_id)
        question_records = [
            record for record in fetched["records"]
            if record["kind"] in {"question.asked", "question.answered"}
        ]
        assert [record["kind"] for record in question_records] == ["question.asked", "question.answered"]
        assert question_records[0]["metadata"]["interaction_id"] == interaction_id
        assert question_records[0]["metadata"]["questions"] == [QUESTION]
        assert question_records[1]["metadata"]["answers"]["format"]["option_id"] == "html"
        assert question_records[1]["metadata"]["status"] == "answered"
    finally:
        runtime.bus.unsubscribe(subscriber)
        await runtime.registry.unload_all()


@pytest.mark.asyncio
async def test_cancel_turn_resolves_pending_question(tmp_path: Path) -> None:
    """Stopping a turn durably resolves the pending question as cancelled."""
    runtime, _provider, session = await _boot(tmp_path, [_question_response()])
    subscriber = runtime.bus.subscribe(interactive=True)
    try:
        turn = asyncio.create_task(
            runtime.commands.submit_turn(
                session_id=session.session_id,
                prompt="Wait for a question then stop.",
                options={"provider": "scripted", "model": MODEL.id},
            )
        )
        await _wait_for_question(subscriber)
        assert await runtime.commands.cancel_turn(session.session_id)
        result = await asyncio.wait_for(turn, timeout=2.0)
        assert result.stop_reason == "cancelled"
        fetched = await runtime.commands.sessions_get(session_id=session.session_id)
        answered = [r for r in fetched["records"] if r["kind"] == "question.answered"]
        assert len(answered) == 1
        assert answered[0]["metadata"]["status"] == "cancelled"
    finally:
        runtime.bus.unsubscribe(subscriber)
        await runtime.registry.unload_all()

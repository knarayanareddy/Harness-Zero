"""Real HTTP/SSE shutdown regressions for HarnessWebServer."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from harness.frontends.web.server import HarnessWebServer
from harness.kernel.approvals import ApprovalBroker
from harness.kernel.bus import EventBus
from harness.kernel.commands import Commands
from harness.kernel.gate import PolicyGate
from harness.kernel.jail import WorkspaceJail
from harness.kernel.registry import PluginRegistry
from harness.kernel.runner import TurnRunner
from harness.kernel.secrets import Secrets
from harness.kernel.session import SessionManager
from harness.kernel.settings import SettingsStore
from harness.sdk.events import Event, SystemNotice


@pytest.fixture
def shutdown_setup(tmp_path: Path) -> tuple[HarnessWebServer, EventBus, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bus = EventBus()
    settings = SettingsStore(tmp_path / "settings.json")
    secrets = Secrets(tmp_path / "secrets.json")
    registry = PluginRegistry(workspace=workspace, settings=settings, secrets=secrets, bus=bus)
    jail = WorkspaceJail(workspace)
    approvals = ApprovalBroker(bus)
    gate = PolicyGate(
        registry=registry,
        jail=jail,
        settings=settings,
        secrets=secrets,
        approvals=approvals,
        bus=bus,
    )
    runner = TurnRunner(registry=registry, gate=gate, settings=settings, bus=bus)
    sessions = SessionManager(workspace=workspace, registry=registry, settings=settings, bus=bus)
    commands = Commands(
        workspace=workspace,
        session_manager=sessions,
        runner=runner,
        gate=gate,
        settings=settings,
        secrets=secrets,
        registry=registry,
        bus=bus,
    )
    server = HarnessWebServer(
        commands=commands,
        bus=bus,
        host="127.0.0.1",
        port=0,
        sandbox_port=0,
        workspace=workspace,
    )
    return server, bus, workspace


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), timeout=timeout)


async def _open_ready_sse(
    server: HarnessWebServer,
    bus: EventBus,
    client: aiohttp.ClientSession,
    *,
    expected_interactive: int = 1,
) -> aiohttp.ClientResponse:
    response = await client.get(
        f"http://127.0.0.1:{server.port}/events",
        headers={"Authorization": f"Bearer {server.auth_token}"},
    )
    assert response.status == 200
    await _wait_until(
        lambda: sum(sub.interactive for sub in bus._subscribers.values()) >= expected_interactive
    )

    bus.publish(
        Event(
            type="system.notice",
            data=SystemNotice(message="SSE ready", level="info", code="shutdown.ready"),
        )
    )
    frame = await asyncio.wait_for(response.content.readuntil(b"\n\n"), timeout=1.0)
    lines = frame.decode("utf-8").splitlines()
    payload = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
    assert payload["type"] == "system.notice"
    assert payload["data"] == {
        "message": "SSE ready",
        "level": "info",
        "code": "shutdown.ready",
    }
    return response


async def _recover_old_shutdown(
    stop_task: asyncio.Task[None], responses: list[aiohttp.ClientResponse], bus: EventBus
) -> bool:
    """Bound the RED cleanup while waking the pre-fix handler using a real notice."""
    try:
        await asyncio.wait_for(asyncio.shield(stop_task), timeout=1.0)
        return True
    except TimeoutError:
        for response in responses:
            response.close()
        await asyncio.sleep(0)
        for attempt in range(2):
            bus.publish(
                Event(
                    type="system.notice",
                    data=SystemNotice(
                        message="cleanup wakeup",
                        level="info",
                        code="shutdown.test_cleanup",
                    ),
                )
            )
            if attempt == 0:
                await asyncio.sleep(0.05)
        await asyncio.wait_for(asyncio.shield(stop_task), timeout=3.0)
        return False


async def _assert_stopped(server: HarnessWebServer, bus: EventBus, workspace: Path) -> None:
    assert bus._subscribers == {}
    assert server._replay_sub is None
    assert server._replay_task is not None and server._replay_task.done()
    assert not server._sse_tasks
    assert not server._request_tasks
    assert not (workspace / ".harness" / "daemon.json").exists()
    for port in (server.port, server.sandbox_port):
        with pytest.raises(OSError):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=1.0)


@pytest.mark.asyncio
async def test_stop_releases_disconnected_pending_sse_handler(
    shutdown_setup: tuple[HarnessWebServer, EventBus, Path],
) -> None:
    server, bus, workspace = shutdown_setup
    await server.start()
    timeout = aiohttp.ClientTimeout(total=None)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            response = await _open_ready_sse(server, bus, client)
            response.close()
            stop_task = asyncio.create_task(server.stop())
            stopped_in_time = await _recover_old_shutdown(stop_task, [response], bus)
            assert stopped_in_time, "server.stop() waited for a disconnected SSE handler's 15s heartbeat"
            await _assert_stopped(server, bus, workspace)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_stop_releases_open_sse_handler_without_client_disconnect(
    shutdown_setup: tuple[HarnessWebServer, EventBus, Path],
) -> None:
    server, bus, workspace = shutdown_setup
    await server.start()
    timeout = aiohttp.ClientTimeout(total=None)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            response = await _open_ready_sse(server, bus, client)
            second_response = await _open_ready_sse(
                server, bus, client, expected_interactive=2
            )
            stop_task = asyncio.create_task(server.stop())
            stopped_in_time = await _recover_old_shutdown(
                stop_task, [response, second_response], bus
            )
            assert stopped_in_time, "server.stop() waited for an open SSE handler's 15s heartbeat"
            await _assert_stopped(server, bus, workspace)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_stop_without_sse_is_bounded_and_concurrent_calls_are_idempotent(
    shutdown_setup: tuple[HarnessWebServer, EventBus, Path],
) -> None:
    server, bus, workspace = shutdown_setup
    await server.start()
    try:
        await asyncio.wait_for(asyncio.gather(server.stop(), server.stop()), timeout=1.0)
    finally:
        await server.stop()
    await asyncio.wait_for(server.stop(), timeout=1.0)
    await _assert_stopped(server, bus, workspace)


@pytest.mark.asyncio
async def test_request_initiated_stop_returns_before_runner_drains_request(
    shutdown_setup: tuple[HarnessWebServer, EventBus, Path],
) -> None:
    server, bus, workspace = shutdown_setup
    handler_task: asyncio.Future[asyncio.Task[None]] = asyncio.get_running_loop().create_future()

    async def initiate_shutdown(_request: aiohttp.web.Request) -> aiohttp.web.Response:
        handler_task.set_result(asyncio.current_task())
        await server.stop()
        return aiohttp.web.Response(text="shutdown started")

    server.app.router.add_get("/test/shutdown", initiate_shutdown)
    await server.start()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as client:
            try:
                async with await asyncio.wait_for(
                    client.get(
                        f"http://127.0.0.1:{server.port}/test/shutdown",
                        headers={"Authorization": f"Bearer {server.auth_token}"},
                    ),
                    timeout=1.0,
                ) as response:
                    assert response.status == 200
                    assert await response.text() == "shutdown started"
            except TimeoutError:
                if handler_task.done():
                    handler_task.result().cancel()
                if server._shutdown_task is not None:
                    await asyncio.wait_for(asyncio.shield(server._shutdown_task), timeout=3.0)
                pytest.fail("request-initiated stop deadlocked while the API runner drained its caller")
        assert server._shutdown_task is not None
        await asyncio.wait_for(asyncio.shield(server._shutdown_task), timeout=1.0)
        await _assert_stopped(server, bus, workspace)
    finally:
        await server.stop()


@pytest.mark.parametrize("failure_phase", ["before", "after"])
@pytest.mark.asyncio
async def test_api_cleanup_failure_still_attempts_all_owned_shutdown_stages(
    shutdown_setup: tuple[HarnessWebServer, EventBus, Path],
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    server, bus, workspace = shutdown_setup
    await server.start()
    assert server.api_runner is not None
    assert server.sandbox_runner is not None
    api_runner = server.api_runner
    sandbox_runner = server.sandbox_runner
    original_api_cleanup = api_runner.cleanup
    original_sandbox_cleanup = sandbox_runner.cleanup
    calls: list[str] = []
    api_original_calls = 0
    sandbox_original_calls = 0

    async def cleanup_with_scoped_fault(runner) -> None:
        nonlocal api_original_calls, sandbox_original_calls
        if runner is api_runner:
            calls.append("api")
            if failure_phase == "before":
                raise RuntimeError("injected API cleanup failure")
            api_original_calls += 1
            await original_api_cleanup()
            raise RuntimeError("injected API cleanup failure")
        if runner is sandbox_runner:
            calls.append("sandbox")
            sandbox_original_calls += 1
            await original_sandbox_cleanup()
            return
        raise AssertionError("unexpected AppRunner cleanup target")

    monkeypatch.setattr(type(api_runner), "cleanup", cleanup_with_scoped_fault)
    daemon_file = workspace / ".harness" / "daemon.json"
    assert daemon_file.exists()

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as client:
            await _open_ready_sse(server, bus, client)
            with pytest.raises(RuntimeError, match="injected API cleanup failure"):
                await server.stop()

            assert calls == ["api", "sandbox"]
            assert api_original_calls == (1 if failure_phase == "after" else 0)
            assert sandbox_original_calls == 1

            with pytest.raises(RuntimeError, match="injected API cleanup failure"):
                await server.stop()
            assert calls == ["api", "sandbox"]
            await _assert_stopped(server, bus, workspace)
    finally:
        # Keep RED runs isolated: use the captured real runner cleanup methods to
        # close anything the pre-fix shared shutdown skipped.
        if server.api_site is not None and server.api_runner is not None:
            if server.api_site in server.api_runner.sites:
                await server.api_site.stop()
        if server.sandbox_site is not None and server.sandbox_runner is not None:
            if server.sandbox_site in server.sandbox_runner.sites:
                await server.sandbox_site.stop()
        await original_api_cleanup()
        await original_sandbox_cleanup()
        server.cleanup_daemon_json()
        await _assert_stopped(server, bus, workspace)


@pytest.mark.asyncio
async def test_unexpected_sse_prepare_error_is_not_returned_as_completed_stream(
    shutdown_setup: tuple[HarnessWebServer, EventBus, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, bus, _workspace = shutdown_setup
    await server.start()
    original_prepare = web.StreamResponse.prepare
    injected = False

    async def fail_first_sse_prepare(response: web.StreamResponse, request: web.Request):
        nonlocal injected
        if response.headers.get("Content-Type") == "text/event-stream" and not injected:
            injected = True
            raise RuntimeError("injected SSE response preparation failure")
        return await original_prepare(response, request)

    monkeypatch.setattr(web.StreamResponse, "prepare", fail_first_sse_prepare)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as client:
            async with await client.get(
                f"http://127.0.0.1:{server.port}/events",
                headers={"Authorization": f"Bearer {server.auth_token}"},
            ) as response:
                body = await response.read()
                assert injected
                assert response.status == 500, (
                    "unexpected SSE setup failure was swallowed and returned a completed empty 200 stream"
                )
                assert b"text/event-stream" not in body
        assert not server._sse_tasks
        assert not server._request_tasks
        assert not bus.has_interactive_subscribers()
    finally:
        await server.stop()

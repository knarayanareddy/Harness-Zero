"""T06: stop owned running processes, not only pending approval requests."""
from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from harness.kernel.bootstrap import bootstrap_kernel
from harness.kernel.gate import ToolContextImpl
from harness.plugins.executors.local.executor import LocalExecutor, LocalProcess
from harness.plugins.tools.shell.tools import handle_run_command
from harness.sdk.testing.scripted import ScriptedProvider
from harness.sdk.types import (
    Done,
    ExecRequest,
    HookEvent,
    HookOutcome,
    RiskClass,
    ToolCall,
    ToolCallEnd,
    ToolResult,
    ToolSpec,
)


@pytest_asyncio.fixture
async def command_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = await bootstrap_kernel(
        workspace=workspace, config_dir=tmp_path / "config", resume_session=False,
        overrides={"kernel.security.preset": "open"},
    )
    try:
        yield runtime
    finally:
        await runtime.registry.unload_all()


async def test_cancel_running_command_prevents_delayed_write(command_runtime):
    runtime = command_runtime
    provider = ScriptedProvider(responses=[
        [ToolCallEnd(id="shell", name="run_command", arguments={
            "command": "printf 'T06_READY\\n'; sleep 1; printf wrote > after-stop.txt",
        }), Done(stop_reason="tool_use")],
        [Done(stop_reason="end_turn")],
    ])
    runtime.registry.register_provider(provider, plugin_name="test_t06")
    session = await runtime.session_manager.create_session()
    subscriber = runtime.bus.subscribe()
    task = asyncio.create_task(runtime.commands.submit_turn(
        session_id=session.session_id, prompt="Run the disposable command",
        options={"provider": "scripted", "model": "scripted-v1"},
    ))
    try:
        async with asyncio.timeout(5):
            while True:
                event = await subscriber.get()
                if event.type == "tool.output.delta" and "T06_READY" in event.data.get("text", ""):
                    break
        start = time.monotonic()
        assert await runtime.commands.cancel_turn(session.session_id)
        # Q-03 bounds owned-process reaping, not the separate T01 STOP-hook
        # latency. The gate emits this fact only after its process cleanup.
        async with asyncio.timeout(2):
            while True:
                terminal = await subscriber.get()
                if terminal.type == "tool.result" and terminal.data.get("call_id") == "shell":
                    break
        assert time.monotonic() - start < 2
        assert terminal.data["metadata"]["termination_confirmed"] is True
        assert terminal.data["is_error"] is True
        assert not (runtime.registry.workspace / "after-stop.txt").exists()
        result = await asyncio.wait_for(task, 5)
        assert result.stop_reason == "cancelled"
        assert not (runtime.registry.workspace / "after-stop.txt").exists()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        runtime.bus.unsubscribe(subscriber)


async def test_turn_cancel_bounds_a_handler_that_ignores_cooperative_signal(command_runtime):
    runtime = command_runtime
    started = asyncio.Event()
    stopped = asyncio.Event()
    handlers = []

    async def stuck(args, ctx):
        handlers.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        return ToolResult.text("never")

    spec = ToolSpec(name="test_stuck", description="bounded test", input_schema={"type": "object"}, risk_class=RiskClass.READ, timeout_s=30)
    runtime.registry._register_tool(spec, stuck, plugin_name="test_t06")
    cancel = asyncio.Event()
    task = asyncio.create_task(runtime.gate.execute(ToolCall(id="stuck", name=spec.name, arguments={}), cancel_event=cancel))
    try:
        await asyncio.wait_for(started.wait(), 2)
        cancel.set()
        result = await asyncio.wait_for(asyncio.shield(task), 2)
        assert result.is_error and result.metadata["cancelled"] is True
        assert not result.metadata.get("timed_out", False)
        assert stopped.is_set()
    finally:
        for owned in [task, *handlers]:
            if not owned.done():
                owned.cancel()
        await asyncio.gather(task, *handlers, return_exceptions=True)


async def test_kill_reaps_group_when_leader_exited_but_child_holds_pipes(tmp_path):
    barrier = tmp_path / "ready"
    child = f"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('CHILD_READY',flush=True); Path({str(barrier)!r}).touch(); time.sleep(30)"
    parent = f"import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-u','-c',{child!r}])\nwhile not Path({str(barrier)!r}).exists(): time.sleep(.01)"
    process = await LocalExecutor(tmp_path).start(ExecRequest(command=[sys.executable, "-u", "-c", parent], cwd=tmp_path, shell=False))
    try:
        async with asyncio.timeout(3):
            ready = False
            async for stream, text in process.output():
                if stream == "stdout" and text.strip() == "CHILD_READY":
                    ready = True
                    break
            assert ready, "setup: child did not reach its signal-handler barrier"
            while process._proc.returncode is None:
                await asyncio.sleep(.01)
        start = time.monotonic()
        await process.kill(grace_s=.2)
        await asyncio.wait_for(process.wait(), 1)
        assert time.monotonic() - start < 1
        assert process._readers_done.is_set()
    finally:
        # This fixture owns the new process group; never signal a broad host pattern.
        with contextlib.suppress(Exception):
            await process.kill(grace_s=0)
        await asyncio.wait_for(process.wait(), 2)


@pytest.mark.parametrize("cancellation", ["command", "task"])
@pytest.mark.parametrize("blocked", [False, True])
async def test_cancellation_during_stop_hook_never_reports_completed(command_runtime, cancellation, blocked):
    runtime = command_runtime
    started, stopped = asyncio.Event(), asyncio.Event()

    class BlockingStop:
        name = "test_blocking_stop"
        events = frozenset({HookEvent.STOP})
        matcher = None
        timeout_s = 5

        async def on(self, event, payload, ctx):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
            return HookOutcome(action="continue")

    # Put the controlled STOP barrier first; do not make reaching it depend on
    # the separate baseline checkpoint-latency problem. No hook is disabled.
    runtime.registry.hooks.insert(0, (BlockingStop(), "test_t06"))
    if blocked:
        class PromptBlock(BlockingStop):
            name = "test_prompt_block"
            events = frozenset({HookEvent.USER_PROMPT_SUBMIT})

            async def on(self, event, payload, ctx):
                return HookOutcome(action="block", reason="controlled blocked prompt")

        runtime.registry.hooks.insert(0, (PromptBlock(), "test_t06"))
    runtime.registry.register_provider(ScriptedProvider(responses=[[Done(stop_reason="end_turn")]]), plugin_name="test_t06")
    session = await runtime.session_manager.create_session()
    task = asyncio.create_task(runtime.commands.submit_turn(
        session_id=session.session_id, prompt="Return then enter the STOP barrier",
        options={"provider": "scripted", "model": "scripted-v1"},
    ))
    try:
        await asyncio.wait_for(started.wait(), 5)
        if cancellation == "command":
            assert await runtime.commands.cancel_turn(session.session_id)
            result = await asyncio.wait_for(asyncio.shield(task), 2)
            assert result.stop_reason == "cancelled"
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert stopped.is_set()
        assert not any(r["kind"] == "turn.completed" for r in session.log.records)
        assert session.session_id not in runtime.runner._active_cancels
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_process_cleanup_completes_after_output_reader_failure(tmp_path):
    code = "import sys,time; print('READY',flush=True); sys.stdout.write('x'*70000); sys.stdout.flush(); time.sleep(30)"
    process = await LocalExecutor(tmp_path).start(ExecRequest(command=[sys.executable, "-u", "-c", code], cwd=tmp_path, shell=False))
    try:
        async with asyncio.timeout(3):
            async for stream, text in process.output():
                if stream == "stdout" and text.strip() == "READY":
                    break
            while not process._reader_task.done():
                await asyncio.sleep(.01)
        await process.kill(grace_s=.1)
        result = await asyncio.wait_for(process.wait(), 1)
        assert result.truncated
        assert result.metadata["output_error"]
    finally:
        with contextlib.suppress(Exception):
            await process.kill(grace_s=0)
        await process._proc.wait()
        # Retrieve the old reader exception too, so RED does not hide task leaks.
        await asyncio.gather(process._reader_task, return_exceptions=True)


async def test_failed_process_termination_is_reported_as_unconfirmed(tmp_path):
    release = asyncio.Event()

    class Process:
        async def output(self):
            yield "stdout", "READY\n"
            await release.wait()

        async def wait(self):
            await release.wait()
            return SimpleNamespace(exit_code=0)

        async def kill(self, grace_s=.5):
            raise PermissionError("synthetic termination refusal")

    class Executor:
        async def start(self, req):
            return Process()

    cancel = asyncio.Event()
    cancel.set()
    ctx = SimpleNamespace(workspace=tmp_path, executor=Executor(), cancel_event=cancel,
                          cancel_reason="turn", track_process=lambda proc: None,
                          emit_output_delta=lambda stream, text: None)
    try:
        result = await handle_run_command({"command": "fixture"}, ctx)
        assert result.is_error
        assert result.metadata["termination_confirmed"] is False
        assert "unconfirmed" in result.output.lower()
    finally:
        release.set()


async def test_cleanup_keeps_process_registered_during_another_kill(tmp_path):
    ctx = ToolContextImpl(call_id="test", workspace=tmp_path, data_dir=tmp_path, capabilities=frozenset(), executor=None)
    started, release = asyncio.Event(), asyncio.Event()
    stopped = []

    class Process:
        def __init__(self, name):
            self.name = name

        async def kill(self, grace_s=.5):
            if self.name == "first":
                started.set()
                await release.wait()
            stopped.append(self.name)

        async def wait(self):
            return None

    ctx.track_process(Process("first"))
    cleanup = asyncio.create_task(ctx.terminate_tracked())
    try:
        await asyncio.wait_for(started.wait(), 1)
        ctx.track_process(Process("late"))
        release.set()
        await cleanup
        await ctx.terminate_tracked()
        assert stopped == ["first", "late"]
    finally:
        release.set()
        await asyncio.gather(cleanup, return_exceptions=True)


async def test_turn_cancellation_interrupts_tool_timeout_grace(command_runtime):
    runtime = command_runtime
    timeout_seen = asyncio.Event()
    handlers = []

    async def ignores(args, ctx):
        handlers.append(asyncio.current_task())
        await ctx.cancel_event.wait()
        timeout_seen.set()
        await asyncio.Event().wait()

    spec = ToolSpec(name="test_timeout_then_cancel", description="test", input_schema={"type": "object"}, timeout_s=.05)
    runtime.registry._register_tool(spec, ignores, plugin_name="test_t06")
    runtime.settings.set_override("kernel.limits.tool_grace_s", 5)
    cancel = asyncio.Event()
    task = asyncio.create_task(runtime.gate.execute(ToolCall(id="test", name=spec.name, arguments={}), cancel_event=cancel))
    try:
        await asyncio.wait_for(timeout_seen.wait(), 2)
        cancel.set()
        result = await asyncio.wait_for(asyncio.shield(task), 2)
        assert result.is_error and result.metadata["cancelled"] is True
        assert result.metadata["timed_out"] is True  # The earlier timeout remains an honest fact.
    finally:
        for owned in (task, *handlers):
            if not owned.done():
                owned.cancel()
        await asyncio.gather(task, *handlers, return_exceptions=True)


async def test_redirected_child_is_stopped_after_leader_and_readers_exit(tmp_path):
    ready = tmp_path / "child.pid"
    child = f"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(30)"
    parent = f"import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-u','-c',{child!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nwhile not Path({str(ready)!r}).exists(): time.sleep(.01)"
    process = await LocalExecutor(tmp_path).start(ExecRequest(command=[sys.executable, "-u", "-c", parent], cwd=tmp_path, shell=False))
    try:
        async with asyncio.timeout(3):
            while not ready.exists() or process._proc.returncode is None or not process._readers_done.is_set():
                await asyncio.sleep(.01)
        child_pid = int(ready.read_text())
        await process.kill(grace_s=.1)
        deadline = asyncio.get_running_loop().time() + .5
        state = ""
        while asyncio.get_running_loop().time() < deadline:
            observed = subprocess.run(["ps", "-p", str(child_pid), "-o", "stat="], capture_output=True, text=True, timeout=1, check=False)
            state = observed.stdout.strip()
            if not state or state.startswith("Z"):
                break
            await asyncio.sleep(.01)
        assert not state or state.startswith("Z"), f"Owned child is still runnable: {state}"
    finally:
        with contextlib.suppress(Exception):
            await process.kill(grace_s=0)
        await process._proc.wait()


async def test_cancelled_cleanup_cannot_abandon_later_snapshot_process(tmp_path):
    ctx = ToolContextImpl(call_id="test", workspace=tmp_path, data_dir=tmp_path, capabilities=frozenset(), executor=None)
    started, release = asyncio.Event(), asyncio.Event()
    stopped = []

    class Process:
        def __init__(self, name):
            self.name = name

        async def kill(self, grace_s=.5):
            if self.name == "first":
                started.set()
                await release.wait()
            stopped.append(self.name)

        async def wait(self):
            return None

    ctx.track_process(Process("first"))
    ctx.track_process(Process("second"))
    cleanup = asyncio.create_task(ctx.terminate_tracked())
    try:
        await asyncio.wait_for(started.wait(), 1)
        cleanup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cleanup
        release.set()
        await asyncio.wait_for(ctx.terminate_tracked(), 1)
        assert stopped == ["first", "second"]
    finally:
        release.set()
        await asyncio.gather(cleanup, return_exceptions=True)


async def test_dead_leader_cannot_authorize_signalling_reused_numeric_group(monkeypatch):
    calls = []

    async def wait():
        return 0

    stale = SimpleNamespace(pid=123456, returncode=0, stdout=None, stderr=None, stdin=None, wait=wait)
    process = LocalProcess("stale", stale, time.time())
    await process._reader_task

    def reused_group_exists(pgid, sig):
        if sig:
            calls.append((pgid, sig))
        # Simulate the OS reporting the numeric ID exists, but as a new group.

    monkeypatch.setattr(os, "killpg", reused_group_exists)
    try:
        await process.kill(grace_s=0)
    except RuntimeError:
        pass  # Explicitly unconfirmed is safer than signalling an unowned group.
    assert calls == [], calls

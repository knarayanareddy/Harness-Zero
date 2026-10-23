"""T06b: status/control are private, argv/stdin are transparent, identity stays live."""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import shutil
import signal
import sys

import pytest

from harness.plugins.executors.local.executor import LocalExecutor
from harness.sdk.process_supervisor import supervisor_command
from harness.sdk.types import ExecRequest


@pytest.mark.parametrize("launcher", ["direct", "dash"])
async def test_supervisor_reports_payload_status_and_waits_for_owned_control(tmp_path, launcher):
    status_read, status_write = os.pipe()
    control_read, control_write = os.pipe()
    inherited = []
    for fd in (status_write, control_read):
        inherited.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10))
    os.close(status_write)
    os.close(control_read)
    payload = "import sys; print('ECHO:'+sys.stdin.readline().strip(),flush=True); sys.exit(7)"
    argv = supervisor_command([sys.executable, "-u", "-c", payload], *inherited)
    assert argv[0] == sys.executable, "Control-plane startup must not depend on a shell's FD dialect"
    if launcher == "dash":
        dash = shutil.which("dash")
        if dash is None:
            pytest.skip("dash-backed portability lane requires a provisioned dash binary")
        argv = [dash, "-c", 'exec "$@"', "dash-supervisor-launch", *argv]
    process = await asyncio.create_subprocess_exec(
        *argv,
        pass_fds=tuple(inherited), start_new_session=True,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    for fd in inherited:
        os.close(fd)
    try:
        process.stdin.write(b"literal; $(not-executed)\n")
        await process.stdin.drain()
        async with asyncio.timeout(5):
            status = await asyncio.to_thread(os.read, status_read, 128)
            while b"HZSUP1 EXIT 7\n" not in status:
                status += await asyncio.to_thread(os.read, status_read, 128)
        assert b"HZSUP1 READY\n" in status
        assert process.returncode is None, "The identity anchor must outlive payload exit"
        os.write(control_write, b"KILL\n")
        stdout, stderr = await asyncio.wait_for(process.communicate(), 2)
        assert stdout == b"ECHO:literal; $(not-executed)\n", stderr
        assert process.returncode == -signal.SIGKILL
    finally:
        os.close(status_read)
        os.close(control_write)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)  # This test still owns its live anchor.
            await process.wait()


async def test_payload_cannot_inherit_control_fds_or_inject_argv(tmp_path):
    payload = "import os,sys,json\nopened=[]\nfor fd in range(3,64):\n try: os.fstat(fd); opened.append(fd)\n except OSError: pass\nprint(json.dumps(opened)); print(sys.argv[1])"
    argument = "literal; $(touch should-not-exist)"
    process = await LocalExecutor(tmp_path).start(ExecRequest(command=[sys.executable, "-c", payload, argument], cwd=tmp_path, shell=False))
    result = await asyncio.wait_for(process.wait(), 3)
    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["[]", argument]
    assert not (tmp_path / "should-not-exist").exists()


async def test_reaped_anchor_is_not_unconfirmed_when_output_completion_is_delayed(tmp_path):
    process = await LocalExecutor(tmp_path).start(ExecRequest(
        command=[sys.executable, "-c", "import sys; sys.stdin.readline(); print('retained output')"],
        cwd=tmp_path, interactive=True, shell=False,
    ))
    drained, release = asyncio.Event(), asyncio.Event()

    class HeldCompletion:
        def set(self):
            drained.set()

        async def wait(self):
            await release.wait()

    process._readers_done = HeldCompletion()
    try:
        await process.send_input("finish\n")
        await asyncio.wait_for(drained.wait(), 3)
        await asyncio.wait_for(asyncio.shield(process._supervisor_task), 3)
        assert process._proc.returncode is not None, "setup: the owned anchor must actually be reaped"
        assert process._supervisor_error is None, "Delayed reader completion is not uncertain process termination"
        release.set()
        result = await process.wait()
        assert result.exit_code == 0
        assert result.stdout == "retained output\n"
    finally:
        release.set()
        await asyncio.gather(process.wait(), return_exceptions=True)


async def test_unreaped_anchor_still_fails_termination_deadline(tmp_path, monkeypatch):
    process = await LocalExecutor(tmp_path).start(ExecRequest(
        command=[sys.executable, "-c", "import sys; sys.stdin.readline()"],
        cwd=tmp_path, interactive=True, shell=False,
    ))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(process, "_request_control", lambda command: None)
            with pytest.raises(TimeoutError):
                await process.kill(grace_s=0)
            assert process._proc.returncode is None
    finally:
        process._request_control("KILL")
        await asyncio.wait_for(process.wait(), 3)


async def test_startup_cancellation_reaps_anchor_and_payload(tmp_path, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    created = []
    original = asyncio.create_subprocess_exec

    async def delayed_transport(*args, **kwargs):
        process = await original(*args, **kwargs)
        created.append(process)
        started.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_transport)
    executor = LocalExecutor(tmp_path)
    task = asyncio.create_task(executor.start(ExecRequest(command=[sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, shell=False)))
    try:
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        await asyncio.wait_for(created[0].wait(), 2)
        assert created[0].returncode == -signal.SIGKILL
        if executor._startup_tasks:
            await asyncio.gather(*executor._startup_tasks, return_exceptions=True)
        assert not executor._startup_tasks
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_lost_owner_control_pipe_stops_the_group(tmp_path):
    process = await LocalExecutor(tmp_path).start(ExecRequest(command=[sys.executable, "-u", "-c", "import time; print('READY',flush=True); time.sleep(30)"], cwd=tmp_path, shell=False))
    try:
        async with asyncio.timeout(3):
            async for stream, text in process.output():
                if stream == "stdout" and text.strip() == "READY":
                    break
        process._close_control()  # Model engine death: the kernel closes its pipes.
        await asyncio.wait_for(process._proc.wait(), 2)
        assert process._proc.returncode == -signal.SIGKILL
        await asyncio.wait_for(process._supervisor_task, 2)
    finally:
        with contextlib.suppress(Exception):
            await process.kill(grace_s=0)


async def test_successful_startup_handoff_cancellation_reclaims_handle(tmp_path, monkeypatch):
    executor = LocalExecutor(tmp_path)
    original = executor._start_supervised
    handles = []
    callers = []

    async def at_handoff(req, cancelled):
        process = await original(req, cancelled)
        handles.append(process)
        asyncio.get_running_loop().call_soon(callers[0].cancel)
        return process

    monkeypatch.setattr(executor, "_start_supervised", at_handoff)
    task = asyncio.create_task(executor.start(ExecRequest(command=[sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, shell=False)))
    callers.append(task)
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert handles
        await asyncio.wait_for(handles[0].wait(), 1)
        assert handles[0]._proc.returncode is not None
    finally:
        for process in handles:
            with contextlib.suppress(Exception):
                await process.kill(grace_s=0)


async def test_second_pipe_failure_closes_first_pipe(tmp_path, monkeypatch):
    original = os.pipe
    opened = []
    count = 0

    def allocation():
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic descriptor exhaustion")
        pair = original()
        opened.extend(pair)
        return pair

    monkeypatch.setattr(os, "pipe", allocation)
    try:
        with pytest.raises(OSError):
            await LocalExecutor(tmp_path).start(ExecRequest(command=["true"], cwd=tmp_path, shell=False))
        for fd in opened:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        for fd in opened:
            with contextlib.suppress(OSError):
                os.close(fd)


@pytest.mark.parametrize("program,expected", [
    ("import os,signal; os.kill(os.getpid(),signal.SIGTERM)", -signal.SIGTERM),
    ("import sys; sys.exit(143)", 143),
])
async def test_direct_exec_preserves_signal_vs_explicit_exit(tmp_path, program, expected):
    process = await LocalExecutor(tmp_path).start(ExecRequest(command=[sys.executable, "-c", program], cwd=tmp_path, shell=False))
    result = await asyncio.wait_for(process.wait(), 3)
    assert result.exit_code == expected


async def test_frozen_helper_uses_trusted_worker_state_not_tool_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "/trusted/engine")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")

    async def capture(*args, **kwargs):
        assert kwargs["env"]["_PYI_ARCHIVE_FILE"] == "/trusted/engine"
        assert kwargs["env"]["_PYI_PARENT_PROCESS_LEVEL"] == "1"
        assert "PYINSTALLER_RESET_ENVIRONMENT" not in kwargs["env"]
        raise OSError("fixture: verified launch without executing a non-frozen Python as a bundle")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    with pytest.raises(OSError):
        await LocalExecutor(tmp_path).start(ExecRequest(command=["true"], cwd=tmp_path, shell=False,
            env_overlay={"_PYI_ARCHIVE_FILE": "attacker", "PYINSTALLER_RESET_ENVIRONMENT": "1"}))


async def test_payload_does_not_inherit_private_bootloader_state(tmp_path):
    payload = "import os,json; print(json.dumps([os.getenv('CUSTOM_VISIBLE'),os.getenv('_PYI_ARCHIVE_FILE'),os.getenv('PYINSTALLER_RESET_ENVIRONMENT')]))"
    process = await LocalExecutor(tmp_path).start(ExecRequest(command=[sys.executable, "-c", payload], cwd=tmp_path, shell=False,
        env_overlay={"CUSTOM_VISIBLE": "ok", "_PYI_ARCHIVE_FILE": "untrusted", "PYINSTALLER_RESET_ENVIRONMENT": "1"}))
    result = await asyncio.wait_for(process.wait(), 3)
    assert result.stdout.strip() == '["ok", null, null]'

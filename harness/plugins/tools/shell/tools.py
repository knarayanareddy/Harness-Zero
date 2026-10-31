"""
Shell execution and task management tool handlers (§5.3, §5.6, PLG-04).
Absorbs TOOL-03 and LOOP-01.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from harness.sdk.context import ToolContext
from harness.sdk.types import ExecRequest, RiskClass, ToolResult, ToolSpec

RUN_COMMAND_SPEC = ToolSpec(
    name="run_command",
    description="Runs a shell command in the workspace via the executor with real-time output streaming and background transition.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command line to execute.",
            },
            "CommandLine": {
                "type": "string",
                "description": "Alternative parameter for command line to execute.",
            },
            "cwd": {
                "type": "string",
                "format": "workspace-path",
                "description": "Working directory path relative to workspace root.",
            },
            "Cwd": {
                "type": "string",
                "format": "workspace-path",
                "description": "Alternative parameter for working directory.",
            },
            "wait_ms_before_async": {
                "type": "integer",
                "description": "Milliseconds to wait synchronously before transitioning to background (default: 10000).",
            },
            "WaitMsBeforeAsync": {
                "type": "integer",
                "description": "Alternative parameter for wait_ms_before_async.",
            },
        },
        "additionalProperties": False,
    },
    risk_class=RiskClass.EXEC,
    capabilities=frozenset(["exec"]),
    timeout_s=600.0,
)

MANAGE_TASK_SPEC = ToolSpec(
    name="manage_task",
    description="Deprecated compatibility alias for the durable job tools (retained for one minor version): query, wait, kill, or send stdin input.",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "wait", "kill", "send_input", "list"],
                "description": "Action to perform on the task (status, wait, kill, send_input, list).",
            },
            "Action": {
                "type": "string",
                "enum": ["status", "wait", "kill", "send_input", "list"],
                "description": "Alternative parameter for action.",
            },
            "task_id": {
                "type": "string",
                "description": "Identifier of the background task.",
            },
            "TaskId": {
                "type": "string",
                "description": "Alternative parameter for task_id.",
            },
            "input": {
                "type": "string",
                "description": "Input text to send to stdin for send_input.",
            },
            "Input": {
                "type": "string",
                "description": "Alternative parameter for input.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Optional timeout in milliseconds for wait action.",
            },
            "TimeoutMs": {
                "type": "integer",
                "description": "Alternative parameter for timeout_ms.",
            },
        },
        "additionalProperties": False,
    },
    risk_class=RiskClass.EXEC,
    capabilities=frozenset(["exec"]),
    timeout_s=300.0,
)


def _register_background_process(
    ctx: ToolContext,
    proc: Any,
    label: str,
    *,
    initial_output: str = "",
) -> str:
    """Adopt a background process into Jobs, preserving output already drained by run_command."""
    jobs = getattr(ctx, "jobs", None)
    if jobs is not None:
        return jobs.register_process(proc, label=label, initial_output=initial_output)
    return ctx.background(proc, label=label)


async def handle_run_command(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    cmd = args.get("command") or args.get("CommandLine", "")
    if not cmd or not str(cmd).strip():
        return ToolResult.error("Error: command argument is required")

    cwd_arg = args.get("cwd") or args.get("Cwd", ".")
    target_cwd = (ctx.workspace / cwd_arg).resolve()
    if not target_cwd.exists() or not target_cwd.is_dir():
        return ToolResult.error(
            f"Error: working directory does not exist or is not a directory: {cwd_arg}"
        )

    wait_ms_val = args.get("wait_ms_before_async")
    if wait_ms_val is None:
        wait_ms_val = args.get("WaitMsBeforeAsync", 10000)
    try:
        wait_ms = int(wait_ms_val)
    except (TypeError, ValueError):
        wait_ms = 10000

    req = ExecRequest(
        command=cmd,
        cwd=target_cwd,
        timeout_s=3600.0,
        interactive=True,
    )
    proc = await ctx.executor.start(req)
    # WS-03: register with the gate's context so a timed-out call stops this child instead of
    # leaving it running with the pipe open.
    track = getattr(ctx, "track_process", None)
    if track is not None:
        track(proc)

    wait_s = max(0.0, wait_ms / 1000.0)
    if wait_s == 0.0:
        task_id = _register_background_process(ctx, proc, str(cmd))
        return ToolResult.text(
            f"Launched background task with ID: {task_id}",
            task_id=task_id,
            background=True,
            sandbox=getattr(proc, "sandbox", None),
        )

    stdout_acc: list[str] = []
    stderr_acc: list[str] = []

    async def _drain() -> None:
        try:
            async for stream, chunk in proc.output():
                if stream == "stdout":
                    stdout_acc.append(chunk)
                else:
                    stderr_acc.append(chunk)
                if hasattr(ctx, "emit_output_delta"):
                    ctx.emit_output_delta(stream, chunk)
        except asyncio.CancelledError:
            pass

    drain_task = asyncio.create_task(_drain())
    wait_proc_task = asyncio.create_task(proc.wait())

    # WS-03: listen for the gate's stop signal while waiting, so a command that outlives its
    # deadline returns the output it already produced instead of being cancelled mid-await.
    cancel_wait: asyncio.Task | None = None
    waiters = [wait_proc_task]
    if isinstance(getattr(ctx, "cancel_event", None), asyncio.Event):
        cancel_wait = asyncio.create_task(ctx.cancel_event.wait())
        waiters.append(cancel_wait)

    done, _ = await asyncio.wait(
        waiters,
        timeout=wait_s,
        return_when=asyncio.FIRST_COMPLETED,
    )

    if cancel_wait is not None and cancel_wait in done:
        confirmed = True
        try:
            await proc.kill(grace_s=0.5)
            await proc.wait()
        except Exception:
            confirmed = False
        wait_proc_task.cancel()
        await asyncio.gather(wait_proc_task, return_exceptions=True)
        drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain_task
        out = "".join(stdout_acc)
        err = "".join(stderr_acc)
        body = out + (f"\n[stderr]\n{err}" if err else "")
        reason = "the turn was cancelled; partial effects may remain" if getattr(ctx, "cancel_reason", None) == "turn" else "the tool call exceeded its deadline"
        state = "stopped" if confirmed else "stop requested; termination unconfirmed"
        return ToolResult.text(
            (body or "(no output)") + f"\n[{state}: {reason}]",
            is_error=True,
            cancelled=True,
            termination_confirmed=confirmed,
            sandbox=getattr(proc, "sandbox", None),
        )
    if cancel_wait is not None:
        cancel_wait.cancel()

    if wait_proc_task in done:
        await drain_task
        res = wait_proc_task.result()
        out = "".join(stdout_acc)
        err = "".join(stderr_acc)
        full = out
        if err:
            full = f"{out}\n[stderr]\n{err}" if out else err
        if not full.strip():
            full = f"(Command exited with code {res.exit_code} and no output)"
        if res.metadata.get("output_error"):
            full += f"\n[output stream failed: {res.metadata['output_error']}; process-group stop requested]"
        return ToolResult.text(
            full,
            is_error=(res.exit_code != 0 or bool(res.metadata.get("output_error"))),
            exit_code=res.exit_code,
            duration_ms=res.duration_ms,
            sandbox=res.metadata.get("sandbox", getattr(proc, "sandbox", None)),
        )
    else:
        # Timed out waiting synchronously; transition process to background
        drain_task.cancel()
        try:
            await drain_task
        except (asyncio.CancelledError, Exception):
            pass

        initial_output = "".join(stdout_acc + stderr_acc)
        task_id = _register_background_process(ctx, proc, str(cmd), initial_output=initial_output)
        return ToolResult.text(
            (
                f"Command still running after {wait_ms}ms. Moved to background job with ID: {task_id}\n"
                "Use job_output or job_list to inspect it; manage_task remains a one-minor-version alias."
            ),
            is_error=False,
            task_id=task_id,
            background=True,
            sandbox=getattr(proc, "sandbox", None),
        )


async def handle_manage_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """One-minor-version compatibility alias for job_list/output/kill."""
    action = args.get("action") or args.get("Action", "list")
    task_id = args.get("task_id") or args.get("TaskId", "")
    jobs = getattr(ctx, "jobs", None)
    notice = "Deprecated: use job_list, job_output, or job_kill; manage_task is retained for one minor version."

    if jobs is not None:
        if action == "list":
            return ToolResult.text(
                notice + "\n" + json.dumps(jobs.list(), indent=2),
                deprecated_alias=True,
            )
        if not task_id:
            return ToolResult.error(f"Error: task_id is required for action '{action}'")
        if action == "status":
            status = jobs.get(task_id)
            if not status:
                return ToolResult.error(f"Error: job '{task_id}' not found")
            state = "running" if status.get("running") else "exited"
            return ToolResult.text(
                f"{notice}\nJob {task_id}: {state}\nExit code: {status.get('exit_code')}\n"
                f"Output tail:\n{status.get('tail', '')}",
                **status,
                deprecated_alias=True,
            )
        if action == "kill":
            if await jobs.kill(task_id):
                return ToolResult.text(f"{notice}\nJob '{task_id}' stopped successfully.", deprecated_alias=True)
            return ToolResult.error(f"Error: unable to stop job '{task_id}' (not found or already exited).")
        if action == "send_input":
            text = args.get("input") or args.get("Input", "")
            if await jobs.send_input(task_id, text):
                return ToolResult.text(f"{notice}\nInput sent to job '{task_id}'.", deprecated_alias=True)
            return ToolResult.error(f"Error: unable to send input to job '{task_id}' (not running or input failed).")
        if action == "wait":
            timeout_ms = args.get("timeout_ms") or args.get("TimeoutMs")
            timeout_s = (float(timeout_ms) / 1000.0) if timeout_ms is not None else None
            try:
                exit_code = await jobs.wait(task_id, timeout_s=timeout_s)
            except TimeoutError:
                return ToolResult.error(f"Timed out waiting for job '{task_id}'.")
            status = jobs.get(task_id) or {}
            return ToolResult.text(
                f"{notice}\nJob '{task_id}' exited with code {exit_code}.\nOutput tail:\n{status.get('tail', '')}",
                exit_code=exit_code,
                job_id=task_id,
                task_id=task_id,
                sandbox=status.get("sandbox"),
                deprecated_alias=True,
            )
        return ToolResult.error(
            f"Error: unknown action '{action}'. Valid actions are: status, wait, kill, send_input, list."
        )

    # Preserve the old in-memory inbox behavior for embedders that have not registered the Jobs service.
    if action == "list":
        tasks = ctx.list_tasks() if hasattr(ctx, "list_tasks") else []
        return ToolResult.text(json.dumps(tasks, indent=2))
    if not task_id:
        return ToolResult.error(f"Error: task_id is required for action '{action}'")
    if action == "status":
        status = ctx.get_task(task_id) if hasattr(ctx, "get_task") else None
        if not status:
            return ToolResult.error(f"Error: task '{task_id}' not found")
        status_str = "running" if status.get("running") else "completed"
        return ToolResult.text(
            f"Task {task_id}: {status_str}\nExit code: {status.get('exit_code')}\nOutput tail:\n{status.get('tail', '')}",
            **status,
        )
    if action == "kill":
        ok = await ctx.kill_task(task_id) if hasattr(ctx, "kill_task") else False
        if ok:
            return ToolResult.text(f"Task '{task_id}' killed successfully.")
        return ToolResult.error(f"Error: unable to kill task '{task_id}' (not found or already completed).")
    if action == "send_input":
        text = args.get("input") or args.get("Input", "")
        ok = await ctx.send_task_input(task_id, text) if hasattr(ctx, "send_task_input") else False
        if ok:
            return ToolResult.text(f"Input sent to task '{task_id}'.")
        return ToolResult.error(f"Error: unable to send input to task '{task_id}' (not running or input failed).")
    if action == "wait":
        timeout_ms = args.get("timeout_ms") or args.get("TimeoutMs")
        timeout_s = (float(timeout_ms) / 1000.0) if timeout_ms is not None else None
        try:
            exit_code = await ctx.wait_task(task_id, timeout_s=timeout_s) if hasattr(ctx, "wait_task") else None
            status = ctx.get_task(task_id) if hasattr(ctx, "get_task") else {}
            status = status or {}
            return ToolResult.text(
                f"Task '{task_id}' completed with exit code {exit_code}.\nOutput tail:\n{status.get('tail', '')}",
                exit_code=exit_code,
                task_id=task_id,
                sandbox=status.get("sandbox"),
            )
        except TimeoutError:
            return ToolResult.error(f"Timed out waiting for task '{task_id}'.")
    return ToolResult.error(
        f"Error: unknown action '{action}'. Valid actions are: status, wait, kill, send_input, list."
    )

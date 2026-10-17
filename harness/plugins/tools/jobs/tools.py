"""Model-facing tools for starting and inspecting durable background jobs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.sdk.context import ToolContext
from harness.sdk.types import ExecRequest, RiskClass, ToolResult, ToolSpec

JOB_START_SPEC = ToolSpec(
    name="job_start",
    description="Starts a background process as a durable session job. Its output is spooled and survives the turn.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1, "description": "Command line to run through the configured executor."},
            "cwd": {"type": "string", "format": "workspace-path", "description": "Workspace directory in which to run the command."},
            "label": {"type": "string", "maxLength": 200, "description": "Short name shown in the Jobs panel."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.EXEC,
    capabilities=frozenset({"exec"}),
    timeout_s=60.0,
)

JOB_OUTPUT_SPEC = ToolSpec(
    name="job_output",
    description="Reads a job's recent output or output bytes after a durable offset.",
    input_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "minLength": 1},
            "tail_lines": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 50},
            "since_offset": {"type": "integer", "minimum": 0, "description": "Optional absolute byte offset in the job's output stream."},
        },
        "required": ["job_id"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.READ,
    capabilities=frozenset(),
    timeout_s=30.0,
)

JOB_LIST_SPEC = ToolSpec(
    name="job_list",
    description="Lists jobs belonging to the current session, including running and recently exited jobs.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    risk_class=RiskClass.READ,
    capabilities=frozenset(),
    timeout_s=15.0,
)

JOB_KILL_SPEC = ToolSpec(
    name="job_kill",
    description="Stops a running background job. This is an execution action and follows normal approval policy.",
    input_schema={
        "type": "object",
        "properties": {"job_id": {"type": "string", "minLength": 1}},
        "required": ["job_id"],
        "additionalProperties": False,
    },
    risk_class=RiskClass.EXEC,
    capabilities=frozenset({"exec"}),
    timeout_s=30.0,
)


async def handle_job_start(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = str(args.get("command") or "").strip()
    if not command:
        return ToolResult.error("Error: command is required")
    jobs = getattr(ctx, "jobs", None)
    if jobs is None:
        return ToolResult.error("Error: the durable jobs service is unavailable")

    cwd_arg = args.get("cwd") or str(ctx.workspace)
    cwd = Path(str(cwd_arg))
    if not cwd.is_absolute():
        cwd = ctx.workspace / cwd
    cwd = cwd.resolve()
    workspace = Path(ctx.workspace).resolve()
    if not cwd.is_relative_to(workspace):
        return ToolResult.error("Error: job working directory must stay inside the workspace")
    if not cwd.exists() or not cwd.is_dir():
        return ToolResult.error(f"Error: working directory is not a directory: {cwd_arg}")

    label = str(args.get("label") or command)[:200]
    req = ExecRequest(command=command, cwd=cwd, timeout_s=None, interactive=True)
    try:
        job = await jobs.start(ctx.executor, req, label=label)
    except Exception as err:
        return ToolResult.error(f"Error starting job: {err}")
    return ToolResult.text(
        f"Started background job {job['job_id']} ({job['label']}). Use job_output or job_list to inspect it.",
        job_id=job["job_id"],
        state=job["state"],
        background=True,
        sandbox=job.get("sandbox"),
    )


async def handle_job_output(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    jobs = getattr(ctx, "jobs", None)
    if jobs is None:
        return ToolResult.error("Error: the durable jobs service is unavailable")
    job_id = str(args.get("job_id") or "")
    result = await jobs.output(
        job_id,
        tail_lines=int(args.get("tail_lines", 50)),
        since_offset=args.get("since_offset"),
    )
    if result is None:
        return ToolResult.error(f"Error: job '{job_id}' was not found in this session")
    output = result.get("output") or "(no output)"
    heading = f"Job {job_id}: {result['state']}"
    if result.get("exit_code") is not None:
        heading += f" (exit {result['exit_code']})"
    if result.get("truncated"):
        heading += " — earlier output was trimmed from the bounded spill file"
    return ToolResult.text(f"{heading}\n{output}", **result)


async def handle_job_list(_args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    jobs = getattr(ctx, "jobs", None)
    if jobs is None:
        return ToolResult.error("Error: the durable jobs service is unavailable")
    return ToolResult.text(json.dumps(jobs.list(), indent=2))


async def handle_job_kill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    jobs = getattr(ctx, "jobs", None)
    if jobs is None:
        return ToolResult.error("Error: the durable jobs service is unavailable")
    job_id = str(args.get("job_id") or "")
    if not await jobs.kill(job_id):
        return ToolResult.error(f"Error: job '{job_id}' was not running or could not be stopped")
    return ToolResult.text(f"Job '{job_id}' stopped.", job_id=job_id, state="exited")

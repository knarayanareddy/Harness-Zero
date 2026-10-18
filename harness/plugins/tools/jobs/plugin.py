"""Plugin entry point for durable background jobs."""
from __future__ import annotations

from harness.sdk.context import PluginContext
from harness.sdk.manifest import PluginManifest

from .registry import JobRegistry
from .tools import (
    JOB_KILL_SPEC,
    JOB_LIST_SPEC,
    JOB_OUTPUT_SPEC,
    JOB_START_SPEC,
    handle_job_kill,
    handle_job_list,
    handle_job_output,
    handle_job_start,
)


class JobsPlugin:
    """Registers the durable jobs service, model tools, and Jobs panel commands."""

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        self.manifest = manifest
        self.jobs: JobRegistry | None = None

    async def setup(self, ctx: PluginContext) -> None:
        self.jobs = JobRegistry(ctx)
        ctx.register_service("jobs", self.jobs)
        for spec, handler in (
            (JOB_START_SPEC, handle_job_start),
            (JOB_OUTPUT_SPEC, handle_job_output),
            (JOB_LIST_SPEC, handle_job_list),
            (JOB_KILL_SPEC, handle_job_kill),
        ):
            ctx.register_tool(spec, handler)

        ctx.register_command(
            "list",
            self.jobs.command_list,
            args_schema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
                "additionalProperties": False,
            },
        )
        ctx.register_command(
            "output",
            self.jobs.command_output,
            args_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "tail_lines": {"type": "integer", "minimum": 1, "maximum": 10000},
                    "since_offset": {"type": "integer", "minimum": 0},
                },
                "required": ["session_id", "job_id"],
                "additionalProperties": False,
            },
        )
        ctx.register_command(
            "kill",
            self.jobs.command_kill,
            args_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "job_id": {"type": "string"},
                },
                "required": ["session_id", "job_id"],
                "additionalProperties": False,
            },
        )

    async def teardown(self) -> None:
        if self.jobs is not None:
            await self.jobs.close()

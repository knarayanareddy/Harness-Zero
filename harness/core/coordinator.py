"""
Reactive Turn Coordinator & Agent Event Loop.
Synthesizes Reactive Wakeup Inbox (0% polling), 5-Tier Prefix Cache,
Progressive Skills, SQLite FTS5 Memory, Tool Execution, and Worktree Sandboxing.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.artifacts.manager import ArtifactManager, ArtifactMetadata
from harness.core.concurrency_gate import ConcurrencyGate
from harness.core.transcript import TranscriptEngine
from harness.core.types import (
    ModelResponse,
    StepSource,
    StepType,
    ToolCall,
    ToolResult,
)
from harness.desktop.macos_bridge import MacOSDesktopBridge
from harness.kernel.validators import validate_session_id
from harness.memory.fts5_index import FTS5MemoryIndex
from harness.models.circuits import IntelligentModelRouter
from harness.models.prefix_cache import PromptCacheBuilder, PromptTiers
from harness.models.router import ModelRouter
from harness.sandboxing.diff_applier import DiffApplier
from harness.sandboxing.executor import BackgroundTask, CommandExecutor
from harness.sandboxing.guardrails import RiskLevel, SecurityPolicy
from harness.sandboxing.ripgrep import RipgrepSearcher
from harness.sandboxing.worktree import WorktreeManager
from harness.skills.registry import SkillRegistry


@dataclass
class TurnResult:
    """Structured turn metadata returned by turn execution."""
    output: str
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "thinking": self.thinking,
            "tool_calls": self.tool_calls,
            "session_id": self.session_id,
            "usage": self.usage,
            "error": self.error,
        }


class AgentCoordinator:
    def __init__(
        self,
        workspace: Path,
        session_id: str | None = None,
        session_dir: Path | None = None,
        router: ModelRouter | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        sid = session_id or f"session_{uuid.uuid4().hex[:12]}"
        validate_session_id(sid)
        self.session_id = sid
        self.session_dir = (
            Path(session_dir)
            if session_dir
            else self.workspace / ".harness" / "sessions" / self.session_id
        )
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # 1. Subsystems
        self.transcripts = TranscriptEngine(self.session_dir)
        self.skills = SkillRegistry(workspace_root=self.workspace)
        self.memory = FTS5MemoryIndex(self.session_dir / "memory.db")
        self.memory.register_conversation(
            conversation_id=self.session_id,
            title="Active Harness Session",
            workspace_uri=str(self.workspace),
        )

        self.executor = CommandExecutor(on_task_completed=self._on_background_task_completed)
        self.worktrees = WorktreeManager(repo_root=self.workspace)
        self.artifacts = ArtifactManager(brain_dir=self.session_dir / "artifacts")
        self.desktop = MacOSDesktopBridge(scratch_dir=self.session_dir / "scratch")
        self.router = router or ModelRouter()
        self.circuit_router = IntelligentModelRouter()
        self.concurrency = ConcurrencyGate(limit=64)
        self.ripgrep = RipgrepSearcher(workspace_root=self.workspace)

        from harness.memory.semantic_memories import SemanticMemoryStore
        from harness.models.cost_tracker import TokenCostTracker
        from harness.orchestration.spawn_ledger import SpawnLedger
        from harness.sandboxing.tirith_security import TirithSecurityCapsule
        from harness.skills.mcp_bridge import MCPBridge

        self.spawn_ledger = SpawnLedger(session_dir=self.session_dir)
        self.cost_tracker = TokenCostTracker()
        self.semantic_memory = SemanticMemoryStore(memory_dir=self.workspace / ".harness" / "memories")
        self.tirith = TirithSecurityCapsule()

        # MCP Integration & Plugin Bridge
        self.mcp = MCPBridge()
        self.mcp.register_server("filesystem_mcp", "npx", ["-y", "@modelcontextprotocol/server-filesystem", str(self.workspace)])
        self.mcp.register_tool(
            "filesystem_mcp",
            "list_directory",
            "List directory entries via Model Context Protocol",
            {"type": "object", "properties": {"path": {"type": "string"}}},
        )
        self.mcp.register_server("database_mcp", "npx", ["-y", "@modelcontextprotocol/server-sqlite", str(self.session_dir / "memory.db")])
        self.mcp.register_tool(
            "database_mcp",
            "query_db",
            "Execute read-only SQL query against SQLite database",
            {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
        )
        self.mcp.register_server("github_mcp", "npx", ["-y", "@modelcontextprotocol/server-github"])
        self.mcp.register_tool(
            "github_mcp",
            "search_repositories",
            "Search GitHub repositories and issues via MCP",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        )

        # 2. Reactive Wakeup Inbox (0% Polling CPU)
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        # Static System Identity
        self.system_identity = """You are Harness-Zero, a sovereign autonomous agent.
Operate with precision, inspect files before modifying them, use surgical search/replace edits,
and verify your work with test runs.
""".strip()

    def list_sessions(self) -> list[dict[str, Any]]:
        """Scans workspace sessions directory and returns historical sessions metadata."""
        sessions_root = self.workspace / ".harness" / "sessions"
        sessions_list: list[dict[str, Any]] = []

        if sessions_root.exists():
            for s_dir in sorted(sessions_root.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
                if not s_dir.is_dir():
                    continue
                sid = s_dir.name
                t_compact = s_dir / "transcript.jsonl"
                title = f"Session {sid[:8]}"
                step_count = 0
                created_at = datetime.fromtimestamp(s_dir.stat().st_mtime, tz=UTC).strftime("%Y-%m-%d %H:%M")

                if t_compact.exists():
                    try:
                        with open(t_compact, encoding="utf-8") as f:
                            lines = [ln.strip() for ln in f if ln.strip()]
                            step_count = len(lines)
                            for ln in lines:
                                try:
                                    rec = json.loads(ln)
                                    if rec.get("type") == "USER_INPUT" and rec.get("content"):
                                        c = rec["content"]
                                        title = c[:48] + ("..." if len(c) > 48 else "")
                                        break
                                except Exception:
                                    pass
                    except Exception:
                        pass

                sessions_list.append({
                    "session_id": sid,
                    "title": title,
                    "step_count": step_count,
                    "is_current": sid == self.session_id,
                    "updated_at": created_at,
                })

        # Ensure current session is always represented
        if not any(s["session_id"] == self.session_id for s in sessions_list):
            sessions_list.insert(0, {
                "session_id": self.session_id,
                "title": f"Session {self.session_id[:8]}",
                "step_count": self.transcripts.step_counter,
                "is_current": True,
                "updated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
            })

        return sessions_list

    def switch_session(self, session_id: str) -> None:
        """Switches active session context, transcripts, memory index, and artifacts atomically."""
        validate_session_id(session_id)
        target_dir = self.workspace / ".harness" / "sessions" / session_id
        target_dir.mkdir(parents=True, exist_ok=True)

        new_transcripts = TranscriptEngine(target_dir)
        new_memory = FTS5MemoryIndex(target_dir / "memory.db")
        new_memory.register_conversation(
            conversation_id=session_id,
            title=f"Harness Session {session_id}",
            workspace_uri=str(self.workspace),
        )
        new_artifacts = ArtifactManager(brain_dir=target_dir / "artifacts")
        new_desktop = MacOSDesktopBridge(scratch_dir=target_dir / "scratch")
        from harness.orchestration.spawn_ledger import SpawnLedger
        new_spawn_ledger = SpawnLedger(session_dir=target_dir)

        # Close old resources if needed
        old_memory = getattr(self, "memory", None)
        old_ledger = getattr(self, "spawn_ledger", None)

        # Atomic state swap
        self.session_id = session_id
        self.session_dir = target_dir
        self.transcripts = new_transcripts
        self.memory = new_memory
        self.artifacts = new_artifacts
        self.desktop = new_desktop
        self.spawn_ledger = new_spawn_ledger

        if old_memory and hasattr(old_memory, "close"):
            try:
                old_memory.close()
            except Exception:
                pass
        if old_ledger and hasattr(old_ledger, "close"):
            try:
                old_ledger.close()
            except Exception:
                pass

    def new_session(self, session_id: str | None = None) -> str:
        """Initializes and activates a new session."""
        new_sid = session_id or f"session_{uuid.uuid4().hex[:12]}"
        validate_session_id(new_sid)
        self.switch_session(new_sid)
        return new_sid

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Returns deterministic, alphabetically-sorted tool definitions."""
        return [
            {
                "name": "edit_file",
                "description": "Applies surgical SEARCH/REPLACE diff blocks to an existing file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "TargetFile": {"type": "string", "description": "Absolute path to file"},
                        "DiffBlock": {
                            "type": "string",
                            "description": "<<<<<<< SEARCH\\n...\\n=======\\n...\\n>>>>>>> REPLACE block",
                        },
                    },
                    "required": ["TargetFile", "DiffBlock"],
                },
            },
            {
                "name": "manage_task",
                "description": "Manage running background tasks: list, status, kill, or send_input.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "Action": {"type": "string", "enum": ["list", "status", "kill", "send_input"]},
                        "TaskId": {"type": "string"},
                        "Input": {"type": "string"},
                    },
                    "required": ["Action"],
                },
            },
            {
                "name": "run_command",
                "description": "Execute a safe shell command with timeouts, PAGER=cat, and async transition.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "CommandLine": {"type": "string"},
                        "Cwd": {"type": "string"},
                        "WaitMsBeforeAsync": {"type": "integer", "default": 5000},
                    },
                    "required": ["CommandLine"],
                },
            },
            {
                "name": "search_codebase",
                "description": "High-performance ripgrep regex search across codebase files matching ChatGPT Desktop.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "Query": {"type": "string", "description": "Search pattern or regex"},
                        "GlobFilter": {"type": "string", "description": "Optional glob filter e.g. '*.py'"},
                        "CaseSensitive": {"type": "boolean", "default": False},
                    },
                    "required": ["Query"],
                },
            },
            {
                "name": "search_memory",
                "description": "Search previous conversation turns and decisions using SQLite FTS5 instant recall.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "Query": {"type": "string"},
                        "Limit": {"type": "integer", "default": 5},
                    },
                    "required": ["Query"],
                },
            },
            {
                "name": "take_screenshot",
                "description": "Capture screen image of the macOS desktop.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "Filename": {"type": "string", "default": "screen.png"},
                    },
                },
            },
            {
                "name": "view_file",
                "description": "View contents of a file with line slicing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "AbsolutePath": {"type": "string"},
                        "StartLine": {"type": "integer", "default": 1},
                        "EndLine": {"type": "integer", "default": 200},
                    },
                    "required": ["AbsolutePath"],
                },
            },
            {
                "name": "view_skill",
                "description": "Progressively read the full playbook instructions for a skill from the catalog.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "SkillName": {"type": "string"},
                    },
                    "required": ["SkillName"],
                },
            },
            {
                "name": "write_to_file",
                "description": "Create a new file or write an artifact.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "TargetFile": {"type": "string"},
                        "CodeContent": {"type": "string"},
                        "Overwrite": {"type": "boolean", "default": True},
                        "Description": {"type": "string", "default": ""},
                        "ArtifactSummary": {"type": "string"},
                    },
                    "required": ["TargetFile", "CodeContent"],
                },
            },
            {
                "name": "call_mcp_tool",
                "description": "Invoke an active Model Context Protocol (MCP) server tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "server": {"type": "string", "description": "MCP server name (e.g. filesystem_mcp, database_mcp, github_mcp)"},
                        "tool": {"type": "string", "description": "MCP tool name"},
                        "arguments": {"type": "object", "description": "Tool input arguments"},
                    },
                    "required": ["server", "tool"],
                },
            },
        ]

    async def execute_turn(
        self,
        user_prompt: str,
        max_tool_iterations: int = 10,
        provider: str | None = None,
        model: str | None = None,
        media: list[dict[str, Any]] | None = None,
    ) -> TurnResult:
        """Executes a reactive agent turn with multi-step tool calls."""
        # 1. Log and index user input
        step_idx, byte_off = self.transcripts.append_step(
            source=StepSource.USER,
            step_type=StepType.USER_INPUT,
            content=user_prompt,
            media=media,
        )
        self.memory.index_turn(
            conversation_id=self.session_id,
            step_index=step_idx,
            transcript_path=self.transcripts.full_file,
            byte_offset=byte_off,
            source=StepSource.USER.value,
            step_type=StepType.USER_INPUT.value,
            content=user_prompt,
        )

        iteration = 0
        final_text = ""
        accumulated_thinking: list[str] = []
        executed_tool_calls: list[dict[str, Any]] = []

        while iteration < max_tool_iterations:
            iteration += 1

            # Build 5-Tier Prefix-Invariant Prompt
            raw_history = self.transcripts.read_compact_history(limit=25)
            formatted_history = []
            for r in raw_history:
                if r["type"] == StepType.USER_INPUT.value:
                    formatted_history.append({"role": "user", "content": r.get("content", "")})
                elif r["type"] == StepType.PLANNER_RESPONSE.value:
                    formatted_history.append({"role": "assistant", "content": r.get("content", "")})
                elif r["type"] == StepType.TOOL_RESULT.value:
                    formatted_history.append({"role": "user", "content": f"[Tool Result]: {r.get('content', '')}"})

            prompt_tiers = PromptTiers(
                static_system_identity=self.system_identity,
                tool_schemas=self.get_tool_schemas(),
                skills_catalog=self.skills.render_catalog(),
                conversation_history=formatted_history,
                current_user_prompt=user_prompt if iteration == 1 else "Continue solving the task.",
            )
            messages = PromptCacheBuilder.build_cached_prompt(prompt_tiers)

            # Determine target provider & model
            endpoint = None
            if self.router._mock_responses:
                target_provider = "mock"
                target_model = "mock-model"
            elif provider and provider != "mock":
                target_provider = provider
                target_model = model or "default"
            elif self.router.default_provider != "mock":
                target_provider = self.router.default_provider
                target_model = self.router.default_model
            else:
                target_provider = "mock"
                target_model = "mock-model"

            # Model Call
            try:
                response: ModelResponse = await self.router.generate(
                    messages=messages,
                    tools=self.get_tool_schemas(),
                    provider=target_provider,
                    model=target_model,
                )
                if endpoint:
                    endpoint.record_success()
            except Exception as e:
                if endpoint:
                    endpoint.record_failure()
                raise e

            # Log Model Response
            serialized_tool_calls = [tc.to_dict() for tc in response.tool_calls]
            p_step, p_off = self.transcripts.append_step(
                source=StepSource.MODEL,
                step_type=StepType.PLANNER_RESPONSE,
                content=response.content,
                thinking=response.thinking,
                tool_calls=serialized_tool_calls,
            )
            self.memory.index_turn(
                conversation_id=self.session_id,
                step_index=p_step,
                transcript_path=self.transcripts.full_file,
                byte_offset=p_off,
                source=StepSource.MODEL.value,
                step_type=StepType.PLANNER_RESPONSE.value,
                content=response.content,
            )

            if response.thinking:
                accumulated_thinking.append(response.thinking)

            final_text = response.content

            # If no tools requested, turn is complete
            if not response.tool_calls:
                break

            # Execute tool calls
            for tool_call in response.tool_calls:
                tool_result = await self._dispatch_tool(tool_call)
                executed_tool_calls.append({
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                    "output": tool_result.output,
                    "is_error": tool_result.is_error,
                })

                # Log Tool Result
                t_step, t_off = self.transcripts.append_step(
                    source=StepSource.TOOL,
                    step_type=StepType.TOOL_RESULT,
                    content=tool_result.output,
                )
                self.memory.index_turn(
                    conversation_id=self.session_id,
                    step_index=t_step,
                    transcript_path=self.transcripts.full_file,
                    byte_offset=t_off,
                    source=StepSource.TOOL.value,
                    step_type=StepType.TOOL_RESULT.value,
                    content=tool_result.output,
                )

        return TurnResult(
            output=final_text,
            thinking="\n\n".join(accumulated_thinking),
            tool_calls=executed_tool_calls,
            session_id=self.session_id,
        )

    async def _dispatch_tool(self, tool_call: ToolCall) -> ToolResult:
        name = tool_call.name
        args = tool_call.arguments

        try:
            if name == "view_file":
                raw_path = args.get("AbsolutePath") or args.get("TargetFile") or args.get("path", "")
                p = Path(raw_path)
                path = p.resolve() if p.is_absolute() else (self.workspace / p).resolve()

                # Red-team credential security check: protect sensitive system paths
                for protected in SecurityPolicy.PROTECTED_SYSTEM_PATHS:
                    try:
                        path.relative_to(protected.resolve())
                        return ToolResult(tool_call.id, name, f"Security Violation: Access to protected system path '{path}' is blocked by SecurityPolicy", is_error=True)
                    except ValueError:
                        pass

                if not path.exists():
                    return ToolResult(tool_call.id, name, f"Error: File {path} does not exist", is_error=True)
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                start = max(1, int(args.get("StartLine", 1)))
                end = min(len(lines), int(args.get("EndLine", len(lines))))
                selected = lines[start - 1 : end]
                numbered = [f"{i}: {line}" for i, line in enumerate(selected, start=start)]
                return ToolResult(tool_call.id, name, "\n".join(numbered))

            elif name == "write_to_file":
                raw_path = args.get("TargetFile") or args.get("AbsolutePath") or args.get("path", "")
                p = Path(raw_path)
                target = p.resolve() if p.is_absolute() else (self.workspace / p).resolve()
                risk = SecurityPolicy.evaluate_file_write_risk(target, self.workspace)
                if risk.requires_approval and risk.level == RiskLevel.DESTRUCTIVE:
                    return ToolResult(tool_call.id, name, f"Security Violation: {risk.reason}", is_error=True)

                content = args.get("CodeContent", "")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

                if args.get("ArtifactSummary"):
                    self.artifacts.write_artifact(
                        target.name,
                        content,
                        ArtifactMetadata(summary=args.get("ArtifactSummary")),
                    )

                return ToolResult(tool_call.id, name, f"File successfully written to {target}")

            elif name == "edit_file":
                raw_path = args.get("TargetFile") or args.get("AbsolutePath") or args.get("path", "")
                p = Path(raw_path)
                target = p.resolve() if p.is_absolute() else (self.workspace / p).resolve()
                diff_block = args.get("DiffBlock", "")
                ok, msg = DiffApplier.apply_diff(target, diff_block)
                return ToolResult(tool_call.id, name, msg, is_error=not ok)

            elif name == "run_command":
                cmd = args.get("CommandLine") or args.get("command", "")
                cwd_raw = args.get("Cwd")
                cwd_path = Path(cwd_raw).resolve() if cwd_raw else self.workspace
                risk = SecurityPolicy.evaluate_command_risk(cmd, cwd_path)
                if risk.requires_approval and risk.level == RiskLevel.DESTRUCTIVE:
                    return ToolResult(tool_call.id, name, f"Blocked Destructive Command: {risk.reason}", is_error=True)

                wait_ms = int(args.get("WaitMsBeforeAsync", 5000))
                res = await self.executor.run(cmd, cwd_path, wait_ms_before_async=wait_ms)
                if res.get("is_background"):
                    return ToolResult(tool_call.id, name, res["message"], task_id=res["task_id"])
                out = res.get("stdout", "")
                err = res.get("stderr", "")
                combined = f"Exit Code: {res.get('exit_code')}\nSTDOUT:\n{out}"
                if err:
                    combined += f"\nSTDERR:\n{err}"
                return ToolResult(tool_call.id, name, combined, is_error=res.get("exit_code") != 0)

            elif name == "view_skill":
                skill_name = args.get("SkillName", "")
                playbook = self.skills.view_skill(skill_name)
                if not playbook:
                    return ToolResult(tool_call.id, name, f"Skill '{skill_name}' not found in registry", is_error=True)
                return ToolResult(tool_call.id, name, playbook)

            elif name == "search_memory":
                q = args.get("Query", "")
                limit = int(args.get("Limit", 5))
                results = self.memory.search(q, limit=limit)
                if not results:
                    return ToolResult(tool_call.id, name, "No memory matches found.")
                lines = ["## Memory Recall Matches:"]
                for r in results:
                    lines.append(f"- Step {r.step_index} [{r.snippet}] (Rank: {r.rank:.2f})")
                return ToolResult(tool_call.id, name, "\n".join(lines))

            elif name == "search_codebase":
                query = args.get("Query", "")
                glob_filter = args.get("GlobFilter")
                case_sensitive = bool(args.get("CaseSensitive", False))
                matches = await self.ripgrep.search(
                    query, glob_filter=glob_filter, case_sensitive=case_sensitive
                )
                if not matches:
                    return ToolResult(tool_call.id, name, "No ripgrep matches found.")
                lines = ["## Ripgrep Codebase Matches:"]
                for m in matches[:30]:
                    lines.append(f"- {m.file_path}:{m.line_number}: {m.line_content}")
                return ToolResult(tool_call.id, name, "\n".join(lines))

            elif name == "manage_task":
                action = args.get("Action", "list")
                task_id = args.get("TaskId", "")
                if action == "list":
                    tasks = self.executor.list_tasks()
                    return ToolResult(tool_call.id, name, json.dumps(tasks, indent=2))
                elif action == "status":
                    t = self.executor.get_task(task_id)
                    if not t:
                        return ToolResult(tool_call.id, name, f"Task {task_id} not found", is_error=True)
                    return ToolResult(tool_call.id, name, f"Task {task_id}: {t.status}\nOutput: {t.stdout}")
                elif action == "kill":
                    ok = await self.executor.kill_task(task_id)
                    return ToolResult(tool_call.id, name, f"Task {task_id} killed: {ok}")

            elif name == "take_screenshot":
                filename = args.get("Filename", "screen.png")
                path = await self.desktop.take_screenshot(filename)
                return ToolResult(tool_call.id, name, f"Screenshot saved at {path}")

            elif name == "call_mcp_tool":
                server = args.get("server", "")
                tool_name = args.get("tool", "")
                tool_args = args.get("arguments", {})
                namespaced = f"{server}__{tool_name}"
                res = await self.mcp.call_tool(namespaced, tool_args)
                return ToolResult(tool_call.id, name, res.get("content", ""), is_error=res.get("is_error", False))

            elif "__" in name or name.startswith("mcp__"):
                res = await self.mcp.call_tool(name, args)
                return ToolResult(tool_call.id, name, res.get("content", ""), is_error=res.get("is_error", False))

            return ToolResult(tool_call.id, name, f"Unknown tool: {name}", is_error=True)

        except Exception as e:
            return ToolResult(tool_call.id, name, f"Error executing tool {name}: {str(e)}", is_error=True)

    async def _on_background_task_completed(self, task: BackgroundTask) -> None:
        """Reactive Wakeup callback from background supervisor."""
        event = {
            "type": "TASK_COMPLETED",
            "task_id": task.task_id,
            "exit_code": task.exit_code,
            "stdout": task.stdout,
            "stderr": task.stderr,
        }
        await self.inbox.put(event)

    async def wait_for_wakeup(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Zero-polling reactive wait on inbox."""
        try:
            if timeout:
                return await asyncio.wait_for(self.inbox.get(), timeout=timeout)
            return await self.inbox.get()
        except TimeoutError:
            return None

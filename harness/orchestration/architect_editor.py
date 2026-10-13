"""
Aider-Style Architect / Editor Dual-Model Self-Healing Loop.
Frontier reasoning model plans architecture; fast model emits search/replace diffs.
Self-healing closed loop tests edits and feeds compiler errors back until green.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from harness.models.router import ModelRouter
from harness.sandboxing.diff_applier import DiffApplier
from harness.sandboxing.executor import CommandExecutor

logger = logging.getLogger("harness.architect_editor")


@dataclass
class EditIteration:
    attempt: int
    diff_block: str
    applied: bool
    test_exit_code: int
    test_output: str
    error_feedback: Optional[str] = None


@dataclass
class ArchitectEditorResult:
    success: bool
    plan: str
    iterations: List[EditIteration]
    commit_sha: Optional[str] = None
    final_message: str = ""


class ArchitectEditorLoop:
    def __init__(
        self,
        workspace_root: Path,
        router: ModelRouter,
        architect_model: str = "claude-3-7-sonnet",
        editor_model: str = "claude-3-5-haiku-latest",
        architect_provider: str = "anthropic",
        editor_provider: str = "anthropic",
    ):
        self.workspace_root = Path(workspace_root)
        self.router = router
        self.architect_model = architect_model
        self.editor_model = editor_model
        self.architect_provider = architect_provider
        self.editor_provider = editor_provider
        self.executor = CommandExecutor()

    async def execute(
        self,
        task_prompt: str,
        target_files: List[Path],
        test_command: Optional[str] = None,
        max_retries: int = 3,
        auto_commit: bool = True,
    ) -> ArchitectEditorResult:
        """
        Step 1: Architect Model formulates design plan.
        Step 2: Editor Model emits search/replace diffs.
        Step 3: Apply diffs.
        Step 4: Run test_command; if fail, feed error back to Editor (self-healing loop).
        Step 5: Git auto-commit once green.
        """
        # Read file contents
        files_context = []
        for p in target_files:
            if p.exists():
                files_context.append(f"### File: {p.name}\n```\n{p.read_text(encoding='utf-8')}\n```")

        # 1. Architect Planning Phase
        arch_prompt = f"""You are the ARCHITECT model.
Analyze the user request and provide a precise architectural modification plan.
Specify which functions, classes, and logic need changes.

User Request: {task_prompt}

Files to modify:
{chr(10).join(files_context)}
"""
        logger.info("[Architect/Editor] Phase 1: Calling Architect model for plan...")
        arch_resp = await self.router.generate(
            messages=[{"role": "user", "content": arch_prompt}],
            provider=self.architect_provider,
            model=self.architect_model,
        )
        plan = arch_resp.content

        # 2. Editor & Self-Healing Loop
        iterations: List[EditIteration] = []
        last_error = ""

        for attempt in range(1, max_retries + 1):
            logger.info(f"[Architect/Editor] Phase 2: Editor attempt {attempt}/{max_retries}...")

            editor_prompt = f"""You are the EDITOR model.
Apply the Architect plan by emitting ONLY surgical SEARCH/REPLACE diff blocks.

Format strictly as:
<<<<<<< SEARCH
exact lines to replace
=======
new lines
>>>>>>> REPLACE

Architect Plan:
{plan}

Files:
{chr(10).join(files_context)}
"""
            if last_error:
                editor_prompt += f"\n\nCRITICAL FIX: The previous diff caused compiler/test failure:\n```\n{last_error}\n```\nFix this error in your new diff!"

            editor_resp = await self.router.generate(
                messages=[{"role": "user", "content": editor_prompt}],
                provider=self.editor_provider,
                model=self.editor_model,
            )
            diff_text = editor_resp.content

            # Apply diff to files
            applied_any = False
            for target_path in target_files:
                ok, _ = DiffApplier.apply_diff(target_path, diff_text)
                if ok:
                    applied_any = True

            # If no test command provided, consider success
            if not test_command:
                iterations.append(
                    EditIteration(attempt, diff_text, applied_any, 0, "No test command specified.")
                )
                commit_sha = None
                if auto_commit:
                    commit_sha = self._git_commit(f"refactor: {task_prompt[:50]}")
                return ArchitectEditorResult(
                    success=applied_any,
                    plan=plan,
                    iterations=iterations,
                    commit_sha=commit_sha,
                    final_message="Diff applied successfully (no test command specified)." if applied_any else "Diff failed to apply cleanly.",
                )

            # Run test / compiler command
            test_res = await self.executor.run(test_command, self.workspace_root)
            exit_code = test_res.get("exit_code", 0)
            stdout = test_res.get("stdout", "")
            stderr = test_res.get("stderr", "")
            combined_output = f"{stdout}\n{stderr}".strip()

            if exit_code == 0:
                iterations.append(
                    EditIteration(attempt, diff_text, applied_any, 0, combined_output)
                )
                logger.info(f"[Architect/Editor] Tests passed cleanly on attempt {attempt}!")

                # Git Auto-Commit
                commit_sha = None
                if auto_commit:
                    commit_sha = self._git_commit(f"refactor: {task_prompt[:50]}")

                return ArchitectEditorResult(
                    success=True,
                    plan=plan,
                    iterations=iterations,
                    commit_sha=commit_sha,
                    final_message=f"Changes successfully applied and verified with tests on attempt {attempt}.",
                )
            else:
                last_error = combined_output
                iterations.append(
                    EditIteration(attempt, diff_text, applied_any, exit_code, combined_output, last_error)
                )
                logger.warning(f"[Architect/Editor] Test failed on attempt {attempt}. Retrying with error feedback...")

        return ArchitectEditorResult(
            success=False,
            plan=plan,
            iterations=iterations,
            final_message=f"Failed to pass tests after {max_retries} attempts.",
        )

    def _git_commit(self, message: str) -> Optional[str]:
        try:
            subprocess.run(["git", "-C", str(self.workspace_root), "add", "-A"], capture_output=True, check=True)
            res = subprocess.run(["git", "-C", str(self.workspace_root), "commit", "-m", message], capture_output=True, text=True, check=True)
            sha_res = subprocess.run(["git", "-C", str(self.workspace_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
            return sha_res.stdout.strip()
        except Exception:
            return None

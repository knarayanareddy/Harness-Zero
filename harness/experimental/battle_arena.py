"""
Multi-Agent Arena & Battle Evaluator (Antigravity Battle Mode Pattern).
Spawns parallel competing subagents in isolated Git worktrees,
evaluates solutions concurrently, and merges the winning branch into main.

Quarantined per EXP-01 / ORCH-02: simulated worker removed, requires explicit worker_fn.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("harness.arena")


class WorktreeManager:
    """Manages isolated git worktrees for parallel agent evaluation."""

    def __init__(self, repo_root: Path, worktree_base_dir: Path | None = None):
        self.repo_root = Path(repo_root)
        self.worktree_base = (
            Path(worktree_base_dir)
            if worktree_base_dir
            else self.repo_root / ".harness" / "worktrees"
        )
        self.worktree_base.mkdir(parents=True, exist_ok=True)
        self._active_worktrees: dict[str, Path] = {}

    def create_worktree(self, subagent_id: str, base_branch: str = "HEAD") -> Path:
        target_dir = self.worktree_base / f"worker_{subagent_id}"
        branch_name = f"harness_worker_{subagent_id}"

        if target_dir.exists():
            self.remove_worktree(subagent_id)

        cmd = [
            "git",
            "-C",
            str(self.repo_root),
            "worktree",
            "add",
            str(target_dir),
            "-b",
            branch_name,
            base_branch,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to create git worktree at {target_dir}: {res.stderr}"
            )

        self._active_worktrees[subagent_id] = target_dir
        return target_dir

    def get_diff(self, subagent_id: str, base_branch: str = "main") -> str:
        branch_name = f"harness_worker_{subagent_id}"
        cmd = ["git", "-C", str(self.repo_root), "diff", f"{base_branch}..{branch_name}"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.stdout

    def remove_worktree(self, subagent_id: str, delete_branch: bool = True) -> None:
        target_dir = self._active_worktrees.get(subagent_id)
        if not target_dir:
            target_dir = self.worktree_base / f"worker_{subagent_id}"

        if target_dir.exists():
            cmd = [
                "git",
                "-C",
                str(self.repo_root),
                "worktree",
                "remove",
                "--force",
                str(target_dir),
            ]
            subprocess.run(cmd, capture_output=True, text=True)
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)

        if delete_branch:
            branch_name = f"harness_worker_{subagent_id}"
            subprocess.run(
                ["git", "-C", str(self.repo_root), "branch", "-D", branch_name],
                capture_output=True,
            )

        self._active_worktrees.pop(subagent_id, None)

    def prune(self) -> None:
        subprocess.run(["git", "-C", str(self.repo_root), "worktree", "prune"], capture_output=True)


@dataclass
class CandidateResult:
    agent_id: str
    strategy: str
    worktree_path: Path
    test_exit_code: int
    test_output: str
    benchmark_score: float
    diff_summary: str = ""
    error_message: str | None = None


@dataclass
class BattleRecord:
    battle_id: str
    prompt: str
    winning_agent_id: str
    candidates: list[CandidateResult]
    duration_seconds: float


class AgentArena:
    def __init__(self, worktree_manager: WorktreeManager):
        self.wt = worktree_manager
        self.history: list[BattleRecord] = []

    async def run_battle(
        self,
        prompt: str,
        candidates: list[dict[str, Any]],
        worker_fn: Callable[[str, Path, str, str], Any] | None = None,
        evaluator_fn: Callable[[list[CandidateResult]], CandidateResult] | None = None,
    ) -> BattleRecord:
        """
        Runs parallel competitive solutions in isolated Git worktrees.
        candidates: [{"id": "subagent_alpha", "strategy": "async_loop"}, ...]
        """
        battle_id = f"battle_{uuid.uuid4().hex[:8]}"
        start_time = time.time()
        tasks = []

        logger.info(f"[{battle_id}] Launching {len(candidates)} parallel competitive workers...")

        for cand in candidates:
            agent_id = cand["id"]
            strategy = cand.get("strategy", "standard")
            wt_path = self.wt.create_worktree(agent_id)
            tasks.append(
                self._run_candidate_worker(agent_id, wt_path, prompt, strategy, worker_fn)
            )

        results: list[CandidateResult] = await asyncio.gather(*tasks)

        # Evaluate winner
        if evaluator_fn:
            winner = evaluator_fn(results)
        else:
            winner = self._default_evaluate_winner(results)

        duration = time.time() - start_time
        record = BattleRecord(
            battle_id=battle_id,
            prompt=prompt,
            winning_agent_id=winner.agent_id,
            candidates=results,
            duration_seconds=duration,
        )
        self.history.append(record)

        # Merge winner worktree if tests passed
        if winner.test_exit_code == 0:
            branch_name = f"harness_worker_{winner.agent_id}"
            subprocess.run(
                ["git", "-C", str(self.wt.repo_root), "merge", "--no-ff", branch_name, "-m", f"Merge winning battle branch {branch_name}"],
                capture_output=True,
            )

        # Cleanup losing worktrees and branches
        for res in results:
            if res.agent_id != winner.agent_id:
                self.wt.remove_worktree(res.agent_id, delete_branch=True)

        return record

    async def _run_candidate_worker(
        self,
        agent_id: str,
        wt_path: Path,
        prompt: str,
        strategy: str,
        worker_fn: Callable[[str, Path, str, str], Any] | None,
    ) -> CandidateResult:
        if worker_fn:
            try:
                res = worker_fn(agent_id, wt_path, prompt, strategy)
                if asyncio.iscoroutine(res):
                    return await res
                return res
            except Exception as e:
                return CandidateResult(
                    agent_id=agent_id,
                    strategy=strategy,
                    worktree_path=wt_path,
                    test_exit_code=1,
                    test_output="",
                    benchmark_score=0.0,
                    error_message=str(e),
                )

        raise ValueError(
            f"No worker_fn provided for candidate '{agent_id}'. Simulated arena workers have been removed."
        )

    def _default_evaluate_winner(
        self, results: list[CandidateResult]
    ) -> CandidateResult:
        passing = [r for r in results if r.test_exit_code == 0]
        if not passing:
            return results[0]
        # Highest benchmark score wins
        return max(passing, key=lambda r: r.benchmark_score)

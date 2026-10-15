import subprocess
from pathlib import Path

import pytest

from harness.experimental.battle_arena import AgentArena, CandidateResult, WorktreeManager


@pytest.mark.asyncio
async def test_battle_arena_parallel_evaluation(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    # Initialize a git repo with main branch
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "arena@test.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Arena Runner"], check=True)

    test_file = repo / "solution.py"
    test_file.write_text("# Baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "solution.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "Baseline commit"], check=True)

    wt = WorktreeManager(repo_root=repo)
    arena = AgentArena(worktree_manager=wt)

    candidates = [
        {"id": "worker_alpha", "strategy": "strategy_standard"},
        {"id": "worker_beta", "strategy": "strategy_optimized"},
    ]

    async def custom_worker(agent_id: str, wt_path: Path, prompt: str, strategy: str) -> CandidateResult:
        sol_path = wt_path / "solution.py"
        sol_path.write_text(f"# Solution by {agent_id}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(wt_path), "commit", "-am", f"Edit by {agent_id}"], check=True)

        score = 95.0 if agent_id == "worker_beta" else 75.0
        return CandidateResult(
            agent_id=agent_id,
            strategy=strategy,
            worktree_path=wt_path,
            test_exit_code=0,
            test_output="Passed",
            benchmark_score=score,
        )

    record = await arena.run_battle("Optimize algorithm", candidates, worker_fn=custom_worker)

    assert record.winning_agent_id == "worker_beta"
    assert len(record.candidates) == 2

    # Winning branch should be merged, losing worktree pruned
    assert not (wt.worktree_base / "worker_worker_alpha").exists()


@pytest.mark.asyncio
async def test_battle_arena_rejects_missing_worker_fn(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "arena@test.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Arena Runner"], check=True)

    test_file = repo / "init.txt"
    test_file.write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "init.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "Initial commit"], check=True)

    wt = WorktreeManager(repo_root=repo)
    arena = AgentArena(worktree_manager=wt)
    candidates = [{"id": "worker_gamma", "strategy": "strategy_standard"}]

    with pytest.raises(ValueError, match="Simulated arena workers have been removed"):
        await arena.run_battle("Solve problem", candidates, worker_fn=None)

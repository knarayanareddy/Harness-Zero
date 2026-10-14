# Git Worktree Executor (`executors.worktree`)

Provides isolated subprocess execution and git worktree lifecycle management:
- Creates isolated git worktrees at `<workspace>/.harness/worktrees/worker_<subagent_id>`.
- Records base commit SHA (`git rev-parse HEAD`) at creation time and diffs against it.
- Prunes stale worktrees and cleans up stale branches.
- Executes commands asynchronously via `asyncio.create_subprocess_exec`.
- Conforms to the `Executor` protocol with `isolation = "worktree"`.

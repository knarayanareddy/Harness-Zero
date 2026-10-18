# WS-10 — Background jobs and session goals

| | |
|---|---|
| Status | WS-10a Jobs executed locally (uncommitted); WS-10b Goals remains specified/not started |
| Depends on | WS-04 (durable `job.*` / `goal.*` records; log-before-execute), WS-03 (cooperative timeout), WS-05 (panel) |
| Effort | 2–3 days jobs + 2–3 days goals (separate PRs) |
| Principles | P1, P2, P3 |
| dsh reference (@86a40220) | `packages/features/jobs/` (`job_start/job_output/job_list/job_kill`; output ring buffer + spill file; job events durable; jobs survive the turn and are reported at the next turn start), `packages/features/goal/` (one goal per session: `goal_set/goal_status/goal_done`; a *round driver* re-prompts the model with the goal + progress until done/blocked/budget; budget in rounds and USD), `client/features/ui-jobs/` |
| Harness-Zero touchpoints | `harness/plugins/tools/shell/tools.py:55` (`manage_task`: status/kill/input over tasks created by `run_command` with `background`), `harness/sdk/context.py:108-127` (`ToolContext.background(proc,label)->task_id`, `get_task/list_tasks/kill_task/send_task_input`), `:172-181` (`wait_task`, `wait_for_wakeup`), `harness/kernel/inbox.py` (`BackgroundInbox`), events `task.started/completed` (live; **not persisted** — a restart forgets running tasks), `harness/kernel/runner.py:65-72` (`max_iterations`), `harness/kernel/commands.py` (`submit_turn`, `cancel_turn`), `plugins/stores/cost_ledger` (per-turn USD → budget source) |

## 1. Outcome

**Jobs (PR A).** A job is a background process whose *existence and output* are durable: `job.started/output/exited` records; output stored in a ring buffer (last `ring_bytes`, default 64 KiB) plus a spill file (`<ws>/.harness/sessions/<sid>/jobs/<job_id>.log`); tools `job_start {command, cwd, label}`, `job_output {job_id, tail_lines|since_offset}`, `job_list`, `job_kill`. On daemon restart, jobs whose process is gone are closed with `job.exited{reason: "lost"}`; the next `turn.started` injects a `system.notice` (model-visible) summarising jobs that finished since the last turn (P1). `manage_task` becomes an alias over jobs for one minor version. The Jobs panel (WS-05 `[ui] panels`) lists jobs with live tail (`job.output` live event) and kill.

**Goals (PR B).** One goal per session: `goal_set {title, done_when, budget: {rounds: 20, usd: 5.0}}`, `goal_status`, `goal_done {summary}` / `goal_blocked {reason}`. A **round driver** in the runner: when a turn ends with a goal active and not done/blocked, and budget remains, the driver submits the next round automatically with prompt `Round k/N for goal "<title>". Progress so far: <last goal_status>. Continue; call goal_done when done_when is satisfied, goal_blocked if you cannot.` Each round is an ordinary turn (same gate, same approvals — approvals pause the driver, P3). Budget exhaustion ⇒ `goal.paused{reason: budget}` and a UI card with "continue (+10 rounds / +$2)". Records: `goal.set/round/status/done/blocked/paused`. `status` shows the goal.

## 2. Current state (verified)

### WS-10a Jobs — executed locally, uncommitted

- The `tools-jobs` plugin registers a session-scoped durable registry, `job_start/output/list/kill`, panel commands, and the plugin-owned Jobs pane. The panel refreshes on `job.started/output/exited`; stop uses the approval broker.
- Job starts, coalesced output ranges, and terminal outcomes are recorded as `job.started/output/exited`. Output is kept in the bounded in-memory ring and session spill file; active snapshots are recovered as `lost` and announced to the model on its next turn.
- Detached `job_start` jobs do not block the current turn or inject same-turn reactive wakeups. Legacy `run_command` background tasks retain reactive waits and the `manage_task` compatibility alias.
- The registry rejects symlinked, non-regular, and multiply-linked state files; all job-data opens use nonblocking/no-follow flags where supported and validate the opened file descriptor. FIFO safety tests cover both the index and spill paths.
- Evidence on the current uncommitted branch: job kernel/restart tests **9 passed**; `job_finishes_between_turns` replay **1 passed**; full `pytest -q` **716 passed, 4 skipped**. `ruff check .`, strict UI audit, `harness plugin doctor --all`, `git diff --check`, and generated catalog drift checks pass. Catalog README/scenario-coverage warnings remain advisory; the Jobs tools are exercised by kernel integration tests, but not all have separate model-replay scenarios.
- These results are local only: no commit or push was made. `npm ci` was run to enable the web DOM scenarios; npm reported 7 dependency audit advisories, and no automatic audit fix was applied.

### WS-10b Goals — not started

- No goal plugin, goal records, or round driver have been added. Goals remain outside the WS-10a edit approval and require their own phase.

## 3. Design

### 3.1 Jobs plugin (`tools/jobs`)

- `JobRegistry` (kernel-side service registered through a new SDK slot `register_service("jobs", obj)` so the runner's turn-start notice and the panel command can reach it without importing the plugin): `start(req, label) -> Job`, `get/list/kill`, `output(job_id, *, tail_lines, since)`.
- Output capture through the executor `Process` streams; each chunk → ring buffer + spill file; `job.output` **live** event every ≤ 250 ms coalesced; `job.exited` durable with exit code, duration, and the last 20 lines.
- Turn-start notice built by the runner via the service: `Jobs finished since your last turn: #3 "pytest" exited 1 after 42s (tail: …). Use job_output for more.`
- Settings `plugins.tools-jobs.{ring_bytes: 65536, max_jobs: 20, spill_max_bytes: 50 MiB}`.

### 3.2 Goal driver (kernel, `harness/kernel/goals.py`)

```python
@dataclass
class Goal: id: str; title: str; done_when: str; budget_rounds: int; budget_usd: float; rounds_used: int = 0; usd_used: float = 0.0; state: Literal["active","done","blocked","paused"] = "active"; last_status: str = ""

class GoalDriver:
    async def on_turn_completed(self, session, turn_result):      # called by runner after turn.completed record
        g = await self.active_goal(session)
        if not g or g.state != "active": return
        g.rounds_used += 1; g.usd_used += turn_result.cost_usd
        if g.rounds_used >= g.budget_rounds or g.usd_used >= g.budget_usd: await self.pause(g, "budget"); return
        await self.log(session, "goal.round", {...})
        await self.commands.submit_turn(session_id=session.id, prompt=round_prompt(g), options={"goal_round": g.rounds_used + 1})
```

Driver triggers are explicit: only `goal_set` activates it; `cancel_turn` pauses it (`goal.paused{reason: user}`); `goal_done/goal_blocked` end it. Nothing runs without a human having asked for a goal.

### 3.3 Tools (`tools/goals` plugin)

`goal_set`, `goal_status {progress}` (model writes its own progress note — becomes `last_status`), `goal_done {summary}`, `goal_blocked {reason}` — all READ risk (they change driver state, not the workspace). A `goals` context source adds a system block `Active goal: … Round k/N. Last status: …` (P1: derived from records).

## 4. Files

### ADD

```
harness/plugins/tools/jobs/{__init__.py,harness_plugin.toml,plugin.py,tools.py,registry.py,settings.schema.json,README.md,tests/test_conformance.py}
harness/plugins/tools/goals/{…,tools.py,context_source.py,…}
harness/kernel/goals.py
harness/kernel/services.py                    # register_service / get_service slot (typed by name)
harness/frontends/web/static/panels/jobs.js, renderers/{job.js,goal.js}
tests/kernel/test_jobs_through_kernel.py
tests/kernel/test_jobs_survive_restart.py
tests/kernel/test_goal_driver.py
tests/scenarios/{job_finishes_between_turns,goal_three_rounds_then_done,goal_budget_pause}/…
```

### MODIFY

```
harness/sdk/context.py                        # register_service; ToolContext.jobs (typed view) ; background() delegates to jobs
harness/kernel/runner.py                      # turn-start jobs notice; GoalDriver.on_turn_completed hook point
harness/kernel/commands.py                    # jobs.list/output/kill (via plugin register_command), goals.get, goals.resume
harness/plugins/tools/shell/tools.py          # manage_task → alias to jobs with deprecation notice
harness/kernel/settings.py                    # kernel.goals.{max_rounds_hard_cap: 100, max_usd_hard_cap: 50}
docs/events.schema.json (gen)
```

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `test_jobs_through_kernel.py::test_job_output_tail_and_spill` | no jobs |
| `test_jobs_survive_restart.py::test_lost_job_is_closed_and_reported_next_turn` | tasks forgotten; model not told |
| `test_goal_driver.py::test_driver_runs_rounds_until_goal_done` (scripted provider: 3 rounds) | no driver |
| `::test_budget_pause_and_resume` | — |
| `::test_approval_pauses_round_not_bypass` (round with `ask` tool waits; deny ends round normally) | — |
| `::test_cancel_turn_pauses_goal` | — |

## 6. Acceptance

### WS-10a Jobs — locally verified

```bash
./.venv/bin/pytest tests/kernel/test_jobs_through_kernel.py tests/kernel/test_jobs_survive_restart.py -q  # 9 passed
./.venv/bin/pytest tests/scenarios/test_scenarios.py -q -k job_finishes_between_turns  # 1 passed
./.venv/bin/pytest -q  # 716 passed, 4 skipped; includes all flow/scenario and frontend tests
./.venv/bin/ruff check .
./.venv/bin/python tests/frontends/ui_audit.py --strict
./.venv/bin/harness plugin doctor --all
./.venv/bin/harness catalog --check
```

The generated catalog's drift, settings, UI, and reference checks pass. Its plugin-README and scenario-coverage diagnostics remain advisory; the `job_start/output/list/kill` tools and `manage_task` alias are covered by kernel integration tests. The Jobs panel ownership, reachable module, command wiring, and static UI contract are covered by UI contribution/contract tests and the strict audit. An interactive browser smoke test was not separately performed. The scenario asserts durable lifecycle semantics, model-visible next-turn notice, and exclusion of legacy `task.*` records; event validation remains active while exact cross-order goldens are disabled for the async `job.exited`/`cost.updated` race.

### WS-10b Goals — pending

The goal-driver and goal-budget acceptance checks below remain specified, unimplemented, and unrun; they are not part of WS-10a.

```bash
./.venv/bin/pytest tests/kernel/test_goal_driver.py -q
./.venv/bin/pytest tests/scenarios -q -k goal
harness ui → "goal: make tests pass, budget 5 rounds" → rounds run, approvals still prompt, card on budget
```

## 7. Non-goals / cautions

- Jobs are not sandbox-exempt: they go through the executor and WS-06 confinement; a job needing network must be started under a preset that allows it (reported in the job record).
- The goal driver never raises budgets by itself; hard caps in `kernel.goals.*` bound what `goal_set` may request.
- No cron/scheduling; no cross-session goals.
- Spill files are session-scoped and cleaned with the session (same policy as WS-03 spill).

## 8. Agent brief

> Implement WS-10 from `review/dsh-integration/WS-10_jobs_and_goals.md` as two PRs. **A (jobs):** `register_service` slot, `tools/jobs` plugin with `JobRegistry`, ring buffer + spill, durable `job.*` records, turn-start notice in the runner, Jobs panel, `manage_task` alias; `test_jobs_survive_restart.py` red first. **B (goals):** `harness/kernel/goals.py` driver with explicit triggers only, `tools/goals` plugin + context source, hard caps, UI renderers; `test_goal_driver.py` red first; approvals must pause rounds. Open PRs `WS-10a: jobs`, `WS-10b: goals`.

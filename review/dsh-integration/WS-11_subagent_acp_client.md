# WS-11 — Subagents: in-process child turns as a tool, and an ACP *client* to run external agents

| | |
|---|---|
| Status | specified |
| Depends on | WS-04 (child transcripts as records), WS-06 (child inherits preset/sandbox), WS-10a (child runs as a job for long tasks) |
| Effort | 2 days (+1 day per external agent verified) |
| Principles | P1, P3, P6 |
| dsh reference (@86a40220) | `packages/features/subagent/` (`spawn_subagent {task, agent, cwd, timeout}`; children are sessions with their own log, parent gets a summary + link; depth/concurrency caps), `packages/features/acp-client/` (runs an ACP agent over stdio: `initialize`, `session/new`, `session/prompt`, streams `session/update`, forwards permission requests to the parent's approval channel), README notes on Claude Code / Codex as subagents |
| Harness-Zero touchpoints | `harness/kernel/runner.py:605-635` (`spawn_child(strategy, prompt, isolation, inherit_history)` — in-process child turn, depth ≤ 2, worktree isolation, child session id `<parent>_<child>`; **not exposed as a tool**), `harness/plugins/bridges/acp/server.py:18-245` (ACP **server**: methods `initialize`, `session/new`, `prompt`, `cancel`, `session/list` — note `prompt`/`cancel` deviate from the ACP spec names `session/prompt`/`session/cancel`), `harness/cli.py:540` (`harness acp`), `harness/experimental/{spawn_ledger,concurrency_gate}.py` (unused caps), `harness/plugins/executors/worktree/` |

## 1. Outcome

**A — `spawn_subagent` tool (in-process).** `spawn_subagent {task, strategy?, isolation: none|worktree, inherit_history: bool, timeout_s}` runs `spawn_child` and returns the child's final text + `{session_id, cost_usd, tools_used, files_changed}`; durable records `subagent.started/finished` in the parent; the child session is a normal session (visible in the sessions panel, its log is the child's log — P1). Caps: depth 2 (existing), concurrency `kernel.subagents.max_concurrent` (2), budget `max_usd_per_child` (1.0, hard cap). Child approvals route to the parent's interaction channel with a "(subagent)" prefix; headless ⇒ deny (P3).

**B — ACP client (`bridges/acp_client`).** Settings `plugins.bridge-acp-client.agents = {"claude": {"command": "claude-code-acp"}, "dsh": {"command": "npx", "args": ["@deepseek-ai/dsh", "acp"]}}`. Tool `spawn_subagent` accepts `agent: "<name>"`; the plugin starts the process (through the executor → sandboxed per WS-06), speaks ACP over stdio, streams `session/update` chunks as `subagent.output` live events, maps `session/request_permission` to the parent's approval broker, and returns the final message. Compatibility with each external agent is **a test, not a claim**: `tests/integration/test_acp_client_<agent>.py` runs only when the agent binary is present (`skipif`) and the README lists what has actually been exercised.

Also fix the server's method names to the spec (`session/prompt`, `session/cancel`) with the old names accepted for one minor version — otherwise our own client can't talk to our own server.

## 2. Current state (verified)

- `spawn_child` exists and is tested at runner level but no tool calls it; child session transcripts land in a derived session id and are not surfaced.
- An ACP server exists; there is no client. Method names are partially non-standard.
- `experimental/spawn_ledger.py` and `concurrency_gate.py` hint at caps but are not wired.

## 3. Design

### 3.1 Tool

```python
SPAWN = ToolSpec(name="spawn_subagent",
  description="Delegate a self-contained task to a subagent and get its result. Use for parallelisable or long investigations; give a complete brief, the child has no access to this conversation unless inherit_history is true.",
  input_schema={"type":"object","required":["task"],"properties":{
     "task":{"type":"string"}, "agent":{"type":"string","description":"'self' (default) or a configured external agent name"},
     "strategy":{"type":"string"}, "isolation":{"type":"string","enum":["none","worktree"],"default":"none"},
     "inherit_history":{"type":"boolean","default":False}, "timeout_s":{"type":"number","default":600}}},
  risk_class=RiskClass.EXEC, timeout_s=3600)
```

`RiskClass.EXEC` so `careful` asks once per spawn; the child's own tool calls go through the child's gate with the parent's preset (`settings` are shared; `permission_mode` cannot be *loosened* by a child).

### 3.2 Kernel

`SubagentService` (registered via `register_service("subagents")`): `spawn(parent_turn_ctx, request) -> SubagentResult`; implements concurrency/budget caps using the experimental `spawn_ledger` (promote to `harness/kernel/subagents.py`, delete from experimental); writes `subagent.started/finished` records to the parent log and `session.meta.parent` on the child.

### 3.3 ACP client protocol subset

`initialize` (protocolVersion, clientCapabilities: fs read/write **off** — the child uses its own tools; we forward nothing), `session/new {cwd, mcpServers: []}`, `session/prompt {sessionId, prompt:[{type:text}]}` → stream of `session/update` notifications (`agent_message_chunk`, `tool_call`, `tool_call_update`) → final `stopReason`. `session/request_permission` → parent approval (options mapped to allow/deny). `session/cancel` on parent cancel/timeout. Anything else ⇒ logged and ignored; unknown required capability ⇒ fail with a clear error (P3).

## 4. Files

### ADD

```
harness/kernel/subagents.py                             # SubagentService, ledger (from experimental), caps
harness/plugins/tools/subagent/{…,tools.py,…}           # spawn_subagent
harness/plugins/bridges/acp_client/{…,client.py,protocol.py,settings.schema.json,README.md,tests/…}
harness/frontends/web/static/renderers/subagent.js      # collapsible child summary with "open session" link
tests/kernel/test_spawn_subagent_through_kernel.py
tests/kernel/test_acp_client_against_own_server.py      # spawns `harness acp` as the external agent — the compat test we control
tests/integration/test_acp_client_dsh.py, test_acp_client_claude.py   # skipif binary missing; never in the hermetic job
tests/scenarios/{subagent_worktree_task,subagent_approval_routed_to_parent}/…
```

### MODIFY

```
harness/plugins/bridges/acp/server.py                   # spec method names + aliases; session/request_permission emitted for ASK decisions
harness/kernel/runner.py                                # spawn_child → SubagentService; child session meta
harness/kernel/settings.py                              # kernel.subagents.{max_concurrent, max_usd_per_child, max_depth}
harness/experimental/                                   # remove spawn_ledger/concurrency_gate after promotion
docs/events.schema.json (gen)
```

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `test_spawn_subagent_through_kernel.py::test_child_result_and_records` | no tool |
| `::test_child_cannot_loosen_permission_mode` | — |
| `::test_concurrency_cap` / `::test_budget_cap` | caps unwired |
| `test_acp_client_against_own_server.py::test_prompt_roundtrip_and_permission_forwarding` | no client; server names non-standard |
| scenario `subagent_approval_routed_to_parent` (web) | — |

## 6. Acceptance

```bash
pytest tests/kernel/test_spawn_subagent_through_kernel.py tests/kernel/test_acp_client_against_own_server.py -q && pytest tests/scenarios -q -k subagent
# with an external agent installed:
pytest tests/integration/test_acp_client_dsh.py -q    # README "Verified agents" table updated from this run's output only
harness catalog --check
```

## 7. Non-goals / cautions

- No agent-to-agent chat; one brief in, one result out.
- External agents run with **their** permissions inside **our** sandbox; document that their own approval UX is replaced by ours only when they implement `session/request_permission`.
- Do not claim Claude Code / Codex / dsh compatibility in docs until the integration test has passed on a recorded run (attach the recording as a scenario fixture if the protocol transcript is small).

## 8. Agent brief

> Implement WS-11 from `review/dsh-integration/WS-11_subagent_acp_client.md` as two PRs. **A:** promote `experimental/spawn_ledger.py` to `harness/kernel/subagents.py`, `SubagentService` with caps, `tools/subagent` plugin, records + renderer, child approvals routed to the parent; tests red first. **B:** fix ACP server method names (aliases kept), `bridges/acp_client` with the protocol subset in §3.3, `test_acp_client_against_own_server.py`, skipped integration tests for external agents, README with an evidence-only "Verified agents" table. Open PRs `WS-11a: spawn_subagent`, `WS-11b: ACP client`.

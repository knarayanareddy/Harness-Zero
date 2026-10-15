# WS-07 — Deliverables: per-turn workspace changes and a `present` tool

| | |
|---|---|
| Status | specified |
| Depends on | WS-04 (`checkpoint.created` / `turn.*` records; cards persisted); WS-05 for the `diffs` panel (can land with a temporary renderer if WS-05 is late) |
| Effort | 1 day (+½ day for the panel) |
| Principles | P1, P2 |
| dsh reference (@86a40220) | `packages/features/workspace-changes/` (turn-level changed-files summary derived from checkpoints; `workspace.changes` session event), `packages/features/deliverables/` (`present_file`-style tool that marks an output as the deliverable, the client opens it), `client/features/ui-workspace-changes/` (panel listing changed files with per-file diff) |
| Harness-Zero touchpoints | `harness/plugins/hooks/checkpoints/shadow_repo.py:31-137` (`ShadowRepo.init_if_needed/snapshot(label)->(sha, prev)/restore(sha)/list_checkpoints(limit)`; git repo under `<ws>/.harness/checkpoints/.git` with worktree = workspace), `harness/plugins/hooks/checkpoints/plugin.py` (snapshots at PRE_TOOL_USE for mutating tools), `harness/sdk/cards.py:66` (`Card.diff(path, unified, added, removed)`), `:76` (`Card.table`), `harness/plugins/tools/artifacts/tools.py:17` (`create_artifact` → `<ws>/.harness/artifacts/`), `harness/frontends/web/server.py:283-284,460-475` (`/artifacts` list/get routes with traversal guard), `static/index.html` `data-tab="diffs"` (placeholder panel, no data source), `harness/kernel/runner.py:715,891` (`turn.started` / `turn.completed` emission points) |

## 1. Outcome

1. At `turn.completed` (and `turn.error`/`cancelled`), the kernel emits a durable **`workspace.changes`** record/event: `{files: [{path, status: added|modified|deleted|renamed, added_lines, removed_lines}], base: <checkpoint sha at turn start>, head: <checkpoint sha at turn end>, truncated: bool}`; the UI renders it as a compact card at the end of the turn ("3 files changed · +41 −7") expandable to per-file unified diffs fetched on demand via a new command `workspace.diff {path, base, head}`.
2. The `diffs` side panel becomes real: lists the latest `workspace.changes` for the session, click → diff; "Restore to before this turn" button → `checkpoints.restore {sha}` (existing shadow-repo capability, currently unreachable from the UI) with an approval card.
3. A **`present`** tool (`risk_class=READ`): the model marks a file (inside the workspace or an artifact) as a deliverable with a one-line caption; emits a durable `deliverable.presented` record; the UI opens it in the file viewer / artifact tab. This replaces the model saying "I wrote the report to X" in prose that users miss.

## 2. Current state (verified)

- The shadow repo exists and snapshots before each mutating tool, but nothing diffs two snapshots, nothing shows the user what changed in a turn, and `restore` has no UI path.
- `Card.diff` is used by `edit_file` for the single edit it just made — per-call, not per-turn; a turn with 12 edits yields 12 cards and no summary.
- Artifacts are served, but the model cannot signal "this one is the result".

## 3. Design

### 3.1 Turn-boundary snapshots

`hooks/checkpoints` subscribes additionally to `USER_PROMPT_SUBMIT` (snapshot `turn-start:<turn_id>`) and `STOP` (snapshot `turn-end:<turn_id>`); both are cheap if nothing changed (git sees no diff → reuse sha). Records `checkpoint.created{label, sha}` (WS-04). The plugin then computes `git diff --numstat --name-status base..head` inside the shadow repo and appends `workspace.changes`. Everything lives in the checkpoints plugin — **no new kernel code** besides the two records and the `workspace.diff` command (served by the plugin through a new `ctx.register_command(name, handler)` SDK call, which WS-05 also needs for panels).

### 3.2 `ctx.register_command` (SDK, small)

```python
# PluginContext
def register_command(self, name: str, handler: Callable[..., Awaitable[Any]], *, args_schema: dict | None = None, exposed: bool = True) -> None
```

Names are namespaced `plugins.<name>.<cmd>`; aliases are not allowed (catalog shows the owner). `Commands.dispatch` falls through to plugin commands after the kernel table. (`commands.list` from WS-05 includes them.)

### 3.3 `present` tool (`tools/deliverables` plugin)

```python
ToolSpec(name="present", description="Mark a file as a deliverable for the user and open it in their viewer. Use when you finish producing the document/image/result the user asked for.",
         input_schema={"type":"object","properties":{"path":{"type":"string"},"caption":{"type":"string","maxLength":200}},"required":["path"]},
         risk_class=RiskClass.READ, capabilities=frozenset({"fs:read"}))
```

Handler: resolve through the jail; if the path is under `.harness/artifacts/` emit the artifact URL, else a `files.read`-able workspace path; append `deliverable.presented{path, caption, kind: artifact|file, size, sha256}`; return `ToolResult` with a `Card.notice("Presented app.py — <caption>")`. Client renderer opens the viewer; multiple presents in a turn stack as chips at the turn end.

### 3.4 Records/events

| kind | durable | payload |
|---|---|---|
| `workspace.changes` | yes | `{turn_id, base, head, files[], added, removed, truncated}` |
| `deliverable.presented` | yes | `{turn_id, path, caption, kind, size, sha256}` |
| `checkpoint.restored` | yes | `{sha, label, files_touched}` |

## 4. Files

### ADD

```
harness/plugins/tools/deliverables/{__init__.py,harness_plugin.toml,plugin.py,tools.py,README.md,tests/test_conformance.py}
harness/plugins/hooks/checkpoints/changes.py              # numstat/name-status parsing, workspace.changes builder, diff command
harness/frontends/web/static/renderers/{workspace_changes.js,deliverable.js}
harness/frontends/web/static/panels/diffs.js
tests/kernel/test_workspace_changes.py
tests/kernel/test_present_tool.py
tests/scenarios/{turn_changes_summary,present_artifact,restore_turn}/…
```

### MODIFY

```
harness/sdk/context.py                 # register_command
harness/kernel/commands.py             # plugin command fallthrough; commands.list includes them
harness/kernel/registry.py             # commands slot
harness/plugins/hooks/checkpoints/plugin.py   # USER_PROMPT_SUBMIT/STOP snapshots; emits workspace.changes; registers plugins.hooks-checkpoints.diff / .restore commands
harness/plugins/hooks/checkpoints/harness_plugin.toml   # [ui] panels diffs (WS-05 format)
harness/plugins/hooks/checkpoints/settings.schema.json  # max_files (200), max_diff_bytes (256 KiB)
docs/events.schema.json (gen)
```

### Skeleton: `changes.py`

```python
async def compute_changes(repo: ShadowRepo, base: str, head: str, *, max_files: int) -> dict:
    rc, numstat, _ = await _run_git(["diff", "--numstat", f"{base}..{head}"], repo.git_dir_cwd)
    rc2, status, _ = await _run_git(["diff", "--name-status", "-M", f"{base}..{head}"], repo.git_dir_cwd)
    files = merge_numstat_and_status(numstat, status)[:max_files]
    return {"base": base, "head": head, "files": files, "added": sum(f["added_lines"] for f in files),
            "removed": sum(f["removed_lines"] for f in files), "truncated": len(files) == max_files}

async def diff_file(repo, base, head, path, *, max_bytes) -> str:   # `git diff base..head -- path`, truncated with marker
```

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `test_workspace_changes.py::test_turn_with_two_edits_emits_one_changes_record` | no record |
| `::test_changes_survive_reload` (`sessions.get` contains it) | — |
| `::test_restore_command_reverts_files_and_logs` | restore unreachable via commands |
| `test_present_tool.py::test_present_artifact_emits_record_and_card` | no tool |
| `test_present_tool.py::test_present_outside_workspace_is_denied` | — (jail) |
| scenario `turn_changes_summary` (web) — timeline golden shows "2 files changed · +3 −1" | — |

## 6. Acceptance

```bash
pytest tests/kernel/test_workspace_changes.py tests/kernel/test_present_tool.py -q && pytest tests/scenarios -q -k "changes or present or restore"
# manual: harness ui → ask the model to edit two files → end-of-turn card "2 files changed"; Diffs panel lists them; Restore → approval → files back
harness catalog --check   # present tool in TOOLS.md with a scenario; plugins.hooks-checkpoints.diff/restore in COMMANDS.md
```

## 7. Non-goals / cautions

- No git integration with the user's own repo (no commits, no staging) — the shadow repo stays private; make sure `.harness/checkpoints` is in the workspace `.gitignore` template.
- Big binary changes: numstat shows `-`; render as "binary" and never try to diff them.
- `present` is not an upload; it points at existing files. Deleting the file later leaves a record whose renderer shows "missing".
- Snapshots at turn boundaries must not block the prompt on huge workspaces: the plugin already has a size guard setting — document the number, and emit `system.notice` (not a crash) when skipped.

## 8. Agent brief

> Implement WS-07 from `review/dsh-integration/WS-07_deliverables_workspace_changes.md` after WS-04. Add `PluginContext.register_command` (namespaced, catalogued). In `hooks/checkpoints`: turn-boundary snapshots, `changes.py`, `workspace.changes` + `checkpoint.restored` records, `diff`/`restore` commands, `[ui] panels diffs`. New plugin `tools/deliverables` with the `present` tool and `deliverable.presented` record. Client renderers + `panels/diffs.js` (use the WS-05 registry if merged, else a minimal `renderRecord` case to be migrated). Tests per §5, red first. Open PR `WS-07: workspace changes + present tool`.

# WS-08 — `ask_user` tool: the model asks a structured question and waits for the answer

| | |
|---|---|
| Status | executed (2026-10-17; verified on the existing WS branch) |
| Depends on | WS-05 (form renderer through the registry) — or land with the existing `cards.js` form renderer and migrate; WS-04 for durable `question.*` records |
| Effort | ½–1 day |
| Principles | P1, P3 |
| dsh reference (@86a40220) | `packages/features/ask-user-question/` (tool with 1–4 questions × 2–4 options + free text, blocks the loop until answered, headless/no-UI ⇒ tool error "no interactive client", answer is logged as a session event), `client/features/ui-ask-user/` |
| Harness-Zero touchpoints | `harness/sdk/cards.py:122` (`Card.form(form_id, schema, submit_label, title)` — exists), `static/cards.js:50,169-210` (form renderer exists, submits to `form.submit`), `harness/kernel/commands.py:388-407` (`form_submit` **only publishes a success notice and returns**; nothing waits on it), `harness/kernel/approvals.py:42-81` (`ApprovalBroker.ask(..., timeout_s)` — the blocking request/response pattern to reuse, `pending()`, non-interactive ⇒ deny), `harness/kernel/inbox.py` (`BackgroundInbox.wait_task` — wake-up pattern), `harness/sdk/context.py` (`ToolContext`; add `ask_user`) |

## 1. Outcome

Tool `ask_user` (READ, no approval needed) that the model calls with up to 4 questions, each with 2–4 options and optional free text; the turn **pauses** until the user answers in the UI or the CLI; the answer comes back as the tool result; both question and answer are durable records and render after reload. Without an interactive client the tool returns an error result `ask_user unavailable: no interactive client connected` within 1 s (P3) so the model can proceed with its best assumption and say so.

## 2. Baseline before implementation (verified)

- The pieces existed but were not connected: `Card.form` rendered, `form.submit` arrived at the kernel and was discarded with a "submitted" notice. No tool emitted a form, and nothing awaited a submission.
- The approval broker already implemented the needed pattern (pending map + future + timeout + non-interactive denial + `approval.required/resolved` events) — generalise rather than duplicate.

## 3. Design

### 3.1 Kernel: `InteractionBroker` (generalised `ApprovalBroker`)

`harness/kernel/interactions.py`: `ask(kind: "approval"|"question", payload, *, timeout_s, session_id) -> Answer`, `resolve(interaction_id, answer)`, `pending()`. `ApprovalBroker` becomes a thin wrapper (same public methods, same events) so nothing else changes. New events `question.asked` / `question.answered` (durable; record kinds `question.asked|answered`), command `question.answer {interaction_id, answers}`. `form.submit` is kept for one minor version as an alias that routes to `question.answer` when `form_id` matches a pending question; otherwise it keeps today's behaviour.

Headless detection: same rule as approvals (`bus.has_interactive_subscriber(session_id)`); `harness run` (non-interactive) ⇒ immediate error result; CLI `interactive` ⇒ prompts via `rich` with numbered options.

### 3.2 Tool (`tools/ask_user` plugin)

```python
ASK_USER = ToolSpec(
  name="ask_user",
  description="Ask the user up to 4 short multiple-choice questions when a decision materially changes what you will build. Prefer sensible defaults over asking; never ask what you can find out from the workspace.",
  input_schema={"type":"object","required":["questions"],"properties":{"questions":{"type":"array","minItems":1,"maxItems":4,"items":{
      "type":"object","required":["id","question","options"],"properties":{
        "id":{"type":"string"},"question":{"type":"string","maxLength":300},
        "options":{"type":"array","minItems":2,"maxItems":4,"items":{"type":"object","required":["id","label"],"properties":{"id":{"type":"string"},"label":{"type":"string","maxLength":80},"description":{"type":"string","maxLength":200}}}},
        "allow_custom":{"type":"boolean","default":True}}}}}},
  risk_class=RiskClass.READ, timeout_s=1800.0)

async def ask_user(args, ctx):
    answer = await ctx.ask_user(questions=args["questions"], timeout_s=min(ctx.remaining_time(), 1800))
    if answer is None:                     # headless or timed out
        return ToolResult.error("ask_user unavailable: no interactive client connected (or no answer in time). Proceed with your best assumption and state it.")
    text = "\n".join(f"{q['question']} → {fmt(answer[q['id']])}" for q in args["questions"])
    return ToolResult(content=[TextBlock(text=f"User answered:\n{text}")], metadata={"answers": answer})
```

`ctx.ask_user` is added to `ToolContext` (kernel impl calls the broker with the session id; the question record is appended **before** the event is published — P1).

### 3.3 UI

Renderer for `question.asked`: a card with radio groups per question, optional text input when `allow_custom`, one submit → `question.answer`. After answer: the card collapses to "You chose: …" (from the `question.answered` record). While pending, the composer shows "waiting for your answer" and the Stop button cancels the turn (which resolves the question with `cancelled`). CLI: `rich` prompt.

## 4. Files

### ADD

```
harness/kernel/interactions.py
harness/plugins/tools/ask_user/{__init__.py,harness_plugin.toml,plugin.py,tools.py,README.md,tests/test_conformance.py}
harness/frontends/web/static/renderers/question.js
tests/kernel/test_ask_user_through_kernel.py
tests/scenarios/{ask_user_answered,ask_user_headless}/…
```

### MODIFY

```
harness/kernel/approvals.py            # wrapper over InteractionBroker
harness/kernel/commands.py             # question.answer; form.submit alias; approvals.list → interactions.list (keep alias)
harness/sdk/context.py                 # ToolContext.ask_user, remaining_time()
harness/kernel/gate.py                 # ToolContext impl wiring
harness/frontends/cli/app.py           # question prompt
docs/events.schema.json (gen)
```

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `test_ask_user_through_kernel.py::test_question_blocks_until_answer_and_returns_it` (web server + `question.answer` command) | no tool |
| `::test_headless_returns_error_within_1s` | — |
| `::test_question_and_answer_are_records` (reload shows both) | — |
| `::test_cancel_turn_resolves_pending_question` | — |
| `tests/kernel/test_approvals.py` (existing) must still pass unchanged | guard for the wrapper |

## 6. Acceptance

```bash
pytest tests/kernel/test_ask_user_through_kernel.py tests/kernel/test_approvals.py -q && pytest tests/scenarios -q -k ask_user
harness run -p mock "…" (mock script calls ask_user) → tool error text in output, turn completes
harness ui → model asks → answer → model continues with the answer visible in the next request (check via scenario `provider.requests.json`)
```

## 7. Non-goals / cautions

- Not a general chat interrupt; one pending question per turn (a second `ask_user` while one is pending returns an error).
- Do not auto-pick the first option on timeout; timeout is an error result (the model must say what it assumed).
- Question text goes into the model request only as the tool result — never as a system block.

## 8. Agent brief

> Implement WS-08 from `review/dsh-integration/WS-08_ask_user_tool.md`. Generalise `ApprovalBroker` into `harness/kernel/interactions.py` without changing approval behaviour (existing approval tests unchanged). Add `ToolContext.ask_user`, the `tools/ask_user` plugin with the exact `ToolSpec` in §3.2, `question.asked/answered` records + events, `question.answer` command, web renderer, CLI prompt. Tests in §5 red first. Open PR `WS-08: ask_user tool`.

## 9. Execution notes — 2026-10-17

Implemented the broker facade, `ToolContext.ask_user()` / `remaining_time()`, plugin, durable question facts, `question.answer` plus `form.submit` compatibility routing, `approvals.list` as an alias of `interactions.list`, CLI and web prompts/renderers, turn cancellation, and a fail-fast headless result. Added fail-first kernel coverage, plugin conformance coverage, a CLI prompt test, and answered/headless scenarios. The shared question painter is `_question.js`, with registered `question.asked.js` and `question.answered.js` renderers so the core renderer map remains one-to-one with durable record kinds. Existing scenario request goldens were regenerated to include the newly advertised `ask_user` tool.

Verification on the working tree:

- `.venv/bin/pytest tests/scenarios -q`: **52 passed, 1 skipped** (flow-of-information suite).
- `.venv/bin/pytest -q --ignore=tests/scenarios`: **641 passed, 3 skipped**.
- `.venv/bin/ruff check .` and `git diff --check`: passed.
- `.venv/bin/harness catalog --check`: drift, settings, UI, and refs passed; the existing README-completeness and scenario-coverage warnings remain.

No commit or push was made; WS-08 is complete on the current PR branch pending the user's approval for any Git operation.

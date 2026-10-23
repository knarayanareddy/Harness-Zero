# Required verification matrix v1 — T03

**Status vocabulary:** local-observed, historical-remote, required-not-run,
baseline-red, unavailable, unsupported. Neither readiness, test collection,
recipe evaluation nor a historical run proves the changed tree passes a lane.

| Lane | Required scope | Evidence now / limits | Gate owner |
|---|---|---|---|
| Deterministic source | Linux/macOS; Python 3.11/3.12; Node 22; isolated HOME/config; all default tests, lint, plugin doctor, catalogue/settings/UI/CSS checks | T01 baseline record; existing remote run is historical, new macOS runs recorded separately | Every PR; T01/T29 |
| Review negatives | Explicit `pytest review/regressions` plus `pytest review/supplemental`; original 19 and subsequently reproduced owned findings | Original 19 baseline-red reproductions retained; scoped local repairs and open supplemental findings tracked in review_regressions.json; no partial count qualifies a release | T02; T04–T26/T29 |
| SDK/ledger scaffolding | Named methods/fields, complete finding/contract/task mapping, diagnostic failures | `tests/test_baseline_contracts.py`, `tests/test_verify_environment.py`; not behavioral closure | T01–T03 |
| Provider wire | Strict local HTTP peer; multiple calls/results, errors, media, fragmentation, cancellation | Existing isolated tests are insufficient; direct adapter probes identify open defects | T12–T16/T22 |
| Live provider | Explicitly configured and budgeted provider/model/version; read -> approved edit -> continuation -> final; media/compaction only if claimed | No vendor credential was used in this work; **required-not-run** for release support claims | T13/T14/T15/T29 |
| MCP | Actual CLI plus real stdio/HTTP peers, canonical allow-list, late-start/disconnect, cancellation | T17 exports, T18a text reads, T18b bounded cancellation/output, T21 late registration and T22 media locally verified; full T18 audit remains open, no full client/live/artifact support claim | T17/T18/T21/T22 |
| ACP | Actual CLI and one pinned supported external client/editor; in-flight cancel/permission | CLI session-new baseline-red; external editor **required-not-run**; no general IDE promise | T19/T20 |
| Studio | Actual browser, two sessions, stale replies, refresh/reconnect during turn/approval; accessibility/viewport | T02 uses jsdom; historical review browser evidence is not a new browser qualification | T23/T24/T37 |
| Linux native sandbox | Provisioned bubblewrap host; nested cwd, outside paths/network, required+unavailable | No local Linux runtime; historical Linux CI skipped ALL FOUR native cases because required namespaces were denied. Native confinement remains unverified | T11/T29 |
| macOS native sandbox | Actual Seatbelt availability probe and confined process; nested cwd/write/deny | T11 native cwd/write/outside-file denial locally observed; see T11_CONFINED_CWD.md. Broader full/native release matrix remains incomplete | T11/T29 |
| Wheel/sdist | Build wheel and sdist-to-wheel; install outside checkout/PYTHONPATH; skills/UI/manifests/CLI | T25 locally verifies direct/rebuilt installed artifacts on3.12 and3.11 (offline3.11 builder overlay). Retained resource-test bytes predate later app changes; final full-product artifact qualification remains required | T25/T29 |
| Frozen macOS | Run exact PyInstaller engine outside checkout; contributions/resources/skills/turn/shutdown | R18 recipe probe baseline-red; native build, signing/notarization **required-not-run** | T26/T29 |
| Fault/soak | 100 cancels, 100 restarts, >=100 turns/>=2h; zero safety/state-loss violations | Explicit Q-01–Q-09 targets in root CONSTRAINTS.md; no completed soak claimed | T29 |

## Provider capability qualification

| Adapter | Intended capabilities to qualify, NOT achieved support | Baseline findings and release limits (local repair status in ledger) |
|---|---|---|
| OpenAI-compatible / OpenRouter / DeepSeek | Text, native tools/results, configured media only if actual endpoint/model supports it; usage/error handling | R2; live endpoint/model-specific proof absent |
| Anthropic | Text, native tools/results, base64 and supported URL images, structured stream termination; thinking/cache only with actual verified contract | R6/R9/R15; no live caching/thinking claim |
| Gemini | Text, tools/results, supported images, typed connect/read failures | R6/R14/R15; “best effort” is not permission to hide failures |
| Ollama | Text, supported native tools or bounded XML fallback, images only on capable models | R6/R10/R15; local service/model availability not established here |
| Mock/scripted/replay | Deterministic fixture behavior and labelled echo only | Never count as live-provider validation or silently fall back from vendor failure |

Configured/reachable/capable are different facts. Unknown pricing is a lower
bound, not a verified spend cap. No untested model/client/version is added to a
supported list merely because discovery returned its name.

## Running without changing the bar

Read `.github/workflows/ci.yml` for exact commands. Use
`python scripts/verify_environment.py` for a read-only prerequisite diagnosis;
it returns nonzero for missing/misaligned baseline tools and prints no secret
values. Prepend that venv's bin directory for child `python` invocations. Install
locked Node dev dependencies and explicitly set SCENARIO_DOM_WAIT_MS=6000.

Retain command, SHA, Python/Node/dependency versions, exit status, elapsed time,
JUnit/log and every skip reason. Run each gate separately while diagnosing so
an early failure does not hide later measurements. Full-suite ceiling is 1,500
seconds; operation-specific limits remain unchanged. If it times out, capture
the last test and stacks/process ownership before stopping owned children;
report incomplete, never passed. Do not weaken `test_headless_returns_error_within_1s`.

Linux catalogue freshness is intentionally platform-specific: the source has a
documented macOS skip because screenshot tools add rows. Treat this separately
from required native-sandbox skips. Historical CI is provenance, not authorization
to push/run a workflow or a substitute for new changed-tree execution.

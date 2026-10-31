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
| Live provider | Explicitly configured and budgeted provider/model/version; read -> approved edit -> continuation -> final; media/compaction only if claimed | Bounded source smoke observed on `cohere/north-mini-code:free`: streaming, read, narrowly approved write, continuation/reload and retained context. No additional credential use planned. Other models, media/compaction and release support remain unverified | T13/T14/T15/T29 |
| MCP | Actual CLI plus real stdio/HTTP peers, canonical allow-list, late-start/disconnect, cancellation | T17 exports, T18a text reads, T18b cancellation/output, T18c canonical audit/read-ledger, T18d typed inline-image delivery, T21 late registration and T22 client-side media locally verified. T18d parent46-case batch passes per Python version; Git/background/official-host and refreshed artifact support remain open | T17/T18/T21/T22 |
| ACP | Actual CLI and one pinned supported external client/editor; in-flight cancel/permission | Source official-SDK interoperability and separate frozen-engine raw ACPv1 text/permission/EOF checks locally observed; external editor **required-not-run**. Permission-dialog cancellation is not a process-cancellation trial; no general IDE promise | T19/T20 |
| Studio | Actual browser, two sessions, stale replies, refresh/reconnect during turn/approval; accessibility/viewport | Five original source-browser stages completed on explicitly bound snapshots. Fresh exact-engine Chromium/HTTP smoke passes; it does not establish native WebKit/editor or all accessibility/viewport gates | T23/T24/T37 |
| Linux native sandbox | Provisioned bubblewrap host; nested cwd, outside paths/network, required+unavailable | No local Linux runtime; historical Linux CI skipped ALL FOUR native cases because required namespaces were denied. Native confinement remains unverified | T11/T29 |
| macOS native sandbox | Actual Seatbelt availability probe and confined process; nested cwd/write/deny | T11 native cwd/write/outside-file denial locally observed; see T11_CONFINED_CWD.md. Broader full/native release matrix remains incomplete | T11/T29 |
| Wheel/sdist | Build wheel and sdist-to-wheel; install outside checkout/PYTHONPATH; skills/UI/manifests/CLI | Fresh direct wheels and sdist-rebuilt wheels on both Python versions pass actual noneditable outside-checkout probes. Exact source/resource scope is recorded in MACOS_ARTIFACT.md; later runtime changes require refreshed artifacts | T25/T29 |
| Frozen macOS | Run exact PyInstaller engine outside checkout; contributions/resources/skills/turn/shutdown | Engine `4f4d1bb5…` passes source-read-denied skill hashes, separate offline ACPv1 checks, unwrapped file-read confinement and Chromium/HTTP turn/shutdown. App assembled and ad-hoc signature verified, not installed/launched. Native WebKit/editor, Developer-ID signing and notarization remain unverified | T26/T29 |
| Fault/soak | 100 cancels, 100 restarts, >=100 turns/>=2h; zero safety/state-loss violations | Fresh Q03/Q05 smokes expose harness setup gaps under repair; no full corpus result. The source soak was found interrupted at91 completed-turn records without a controller or terminal/resource report. Failed/incomplete evidence is archived; no completed endurance or resource-growth verdict is claimed | T29 |

## Provider capability qualification

| Adapter | Intended capabilities to qualify, NOT achieved support | Baseline findings and release limits (local repair status in ledger) |
|---|---|---|
| OpenAI-compatible / OpenRouter / DeepSeek | Text, native tools/results, configured media only if actual endpoint/model supports it; usage/error handling | Bounded OpenRouter `cohere/north-mini-code:free` source smoke locally observed; no general provider/model/media or current pricing claim |
| Anthropic | Text, native tools/results, base64 and supported URL images, structured stream termination; thinking/cache only with actual verified contract | R6/R9/R15; no live caching/thinking claim |
| Gemini | Text, tools/results, supported images, typed connect/read failures | R6/R14/R15; “best effort” is not permission to hide failures |
| Ollama | Text, supported native tools or bounded XML fallback, images only on capable models | R6/R10/R15; local service/model availability not established here |
| Mock/scripted/replay | Deterministic fixture behavior and labelled echo only | Never count as live-provider validation or silently fall back from vendor failure |

Configured/reachable/capable are different facts. Unknown pricing is a lower
bound, not a verified spend cap. No untested model/client/version is added to a
supported list merely because discovery returned its name.

Current evidence details: [macOS artifact](MACOS_ARTIFACT.md),
[ACP compatibility](ACP_COMPATIBILITY.md), [T18 audit/read ledger](T18C_MCP_AUDIT.md)
and `tasks/todo.md`. G1 remains non-green: the latest reviewed 867-file full run
passes1280 tests on Python3.12. An instrumented3.11 diagnostic passes1280, but its
single uninstrumented confirmation passes1278 with two MCP timing failures
(200ms cancellation and1s headless reply bounds). Earlier Git/DOM failures pass
that confirmation; their original causes remain unknown and evidence is retained.
The original headless question deadline test passes current lanes. Five
Linux-specific skips per lane are not Linux passes.
These results exclude the seven later main-checkout filmmaking-skill additions;
neither newer documentation nor isolated runtime repairs inherit full qualification.

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

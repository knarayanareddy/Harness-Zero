# Active task status

- **2026-11-03 QA33 repairs implemented and live-verified locally:** reviewed
  native OpenAI defaults, structured compaction history, bounded active-request
  retention and safe transient-error classification are integrated. Final source
  suites pass 1,443/5 skips per baseline; all 13 CI-derived commands pass. New
  wheel `0d116519…9bde8cf` passes 250 installed-artifact tests, default live ACP
  chat/read/edit/permission flows on Luna and Terra, both compaction checkpoints,
  all 12 coding milestones and 500 MCP tool requests. The raw task score stays
  18/19 for a full-fact-versus-identifier reply-format mismatch; a separate clarified
  probe retains exact equality and passes all three plugin lifecycle cases.
  Original failures and QA32/user work remain preserved; no commit/push or new
  official two-hour soak. Evidence:
  `.venv/evidence/qa33-live-e2e-repair-20261102/verification-r3/REPORT.md`.

- **QA32 reviewed diagnostic integrated; full source gates next:** independent
  full-callpath review cleared candidate
  `40b7c0e46d16150dc953ca43d9c14d13a74c380847045a58becbd415940dac73`.
  Parent preimages matched the published base, the exact two-file patch was
  integrated, and 152 affected controls pass on each baseline with Ruff clean.
  The original resource evaluator and sampler remain unchanged. Freeze the
  coding-only full source for serial baseline gates, then prepare a clean pinned
  proposal. No new soak, commit or push is authorized by preparation.

- **QA32 Slice 3 prepared; integrated-path review pending:** opt-in mode,
  lifecycle collection, parent-observed worker launch receipt and the enabled
  terminal evidence gate are implemented in the isolated candidate
  `40b7c0e46d16150dc953ca43d9c14d13a74c380847045a58becbd415940dac73`.
  Parent checked raw JUnit: 101 FD plus 51 unchanged soak controls pass on each
  baseline, zero skips; old resource evaluator/sampler ASTs and soak-test bytes
  are unchanged. Startup-only native collection is not endurance evidence.
  Independent review, parent integration, full source gates and a clean pinned
  proposal remain pending. No new soak, commit or push has occurred.

- **QA32 preparation, Slice 3 assigned:** the collector and artifact primitives
  are independently cleared, including the corrected kill-before-unconfirmed-
  reap control. Exact two-file preimage:
  `68c5f7057a9a405ee72939128936d9e7df0817c6aafcb2545cdf846c89be836f`.
  The next isolated slice wires the explicit opt-in, actual collection phases,
  parent-observed worker PID and mandatory terminal evidence gate. Preparation
  remains incomplete; no new soak, source integration, commit or push yet.

- **QA32 preparation, Slice 2 under review corrections:** the isolated
  descriptor parser/codec foundation was independently cleared. Bounded
  self-process collection and private artifact writing/readback are now prepared
  in the same worktree, with 73 FD controls plus 51 unchanged soak controls
  passing on each Python baseline and Ruff passing. Exact two-file candidate:
  `5761477a319da02f2ad79ae595dda12f2bcb5ee965a2c8369886dd6c2d7ac40f`.
  Independent review requires enforcing the ten-second ceiling before helper
  launch, closing a child-directory FD on metadata-inspection failure, and
  covering cleanup failure, stderr overflow and concurrent pipe draining.
  These findings affect the new, unintegrated diagnostic, not the old soak.
  Parent integration, CLI/worker/lifecycle wiring, fresh source gates and a new
  soak are not done. No commit/push or new long-run execution is authorized.

  Review corrections are implemented in candidate
  `7f8ae38e633d2863d1696035e5b53e9f9173d27f696499197d67daccaf9d02f7`:
  81 FD controls plus 51 unchanged soak controls pass on each baseline, including
  the timeout ceiling, child-directory cleanup and pipe/error branches. The
  prior candidate is archived. Independent review of the revised bytes is still
  required before Slice 3; passing focused tests alone is not clearance.

- **2026-11-02 QA32 preparation requested:** the owner chose stronger descriptor
  evidence rather than accepting the bounded-SQLite explanation as closure.
  The completed soak remains 120 turns / 7226.308 seconds, no integrity errors,
  but `review-required` for one FD increase. Bounded probes reproduce a retained,
  reused `memory.db` descriptor released at unload; the original FD identity is
  still unproven. Prepare opt-in per-descriptor/lifecycle capture, negative
  controls and a reviewed execution plan under the [QA32 spec](../docs/specs/2026-11-02-endurance-fd-evidence.md).
  No new soak is launched or authorized by this preparation; no metrics or
  acceptance limits may be relaxed. Earlier source/Q03/Q05 passes remain scoped.

- **2026-11-01 final-source Q03 cancellation passes 100/100:** source
  `2866c968…95e30f` and unchanged carrier complete one fixed-count run, exit zero,
  max accepted-cancel-to-reap 39.291 ms against 2000 ms. All 300 observed owned
  identities are reaped; all delayed markers remain absent after the barrier.
  Canonical termination results are confirmed; no per-trial kernel signal audit
  or TERM/KILL breakdown is claimed. Exact launchd service absence and source
  integrity are verified. Evidence: `.venv/evidence/qa31-q03-full100-20261101/`.
  This supersedes older Q03 pending statements below. Endurance remains open.

- **2026-11-01 GitHub checkpoint scope:** the owner requested committing and
  publishing the coding work. The reviewed RepoMap repair is committed as
  `29c123a`; the earlier accumulated checkpoint commits are included. Current
  results and remaining gates are summarized in
  [the November 1 checkpoint](../docs/testing/CHECKPOINT_2026-11-01.md).
  Publication is a development checkpoint, not release certification. Seven
  filmmaking files and ignored private verification artifacts remain excluded.

- **2026-11-01 Q05 locally qualifies on the final reviewed source:** one
  controlled full run accepts 100/100 pairs (200 crash/restart transitions) on
  source `2866c968…95e30f` with the reviewed observer. Parent independently
  validates all 100 closed repair traces, checks 401 complete worker cleanup
  receipts and confirms the exact one-shot launchd service is absent. No retry,
  top-up, missing record or output overflow is reported. Runtime evidence is
  retained. The shell-selector warning remains a native-shell caveat; this is
  same-filesystem process-restart qualification, not power-loss durability or
  native/artifact/release support. Older 14/100 and 9/100 failure causes remain
  unknown; this pass does not erase or explain them.
  Receipt: `.venv/evidence/qa31-q05-controlled-full100-20261101/attempt-01/parent-independent-acceptance-20261101.json`.
  Final-source Q03 cancellation and uninterrupted endurance remain next.

- **2026-11-01 QA31-B2 complete instrument acceptance passes 20/20:** the
  owner-approved whole-contract rework reserves the truncation marker budget,
  preserves all ten controls and adds two stronger marker assertions. Exact
  candidate `8e98cb8db98b1098c0653c50e3d163587ef6f0b2c4e9342ff1f854d637564b23`
  is independently reviewed, Ruff-clean per baseline, and passes ten controls
  each on Python 3.11.17/3.12.15 in fresh per-case interpreters. All earlier
  failed candidates and control logs are retained. This qualifies the private
  measuring instrument only; it does not identify the recovery stall's cause.
  One source-bound synthetic recovery smoke is authorized next, with unchanged
  deadlines and no automatic corpus/retry/top-up. No Q05 or endurance pass yet.
  Evidence: `.venv/evidence/qa31-observer-b2-controls-20261101/controls-receipt.json`.

- **2026-11-01 local source gates pass on the reviewed RepoMap composition:**
  candidate `2866c9684977a71042e4910e3c04e25dd0c17eb5c593793f4d9825114895e30f`
  (868 tracked files) completes one serial default run per baseline: 1323 pass,
  5 skip, zero failures/errors each. Ruff, import boundaries, plugin doctor,
  plugin tests and strict UI audit pass each. Static inputs retain the earlier
  verified CSS-freshness evidence. Parent independently checked JUnit hashes,
  counts and exact launchd-service absence. The supervisor summary's metadata
  `KeyError: manifest_file_count` is retained separately; actual controller and
  launchd exit zero, with one run and verified cleanup. No suite was relaunched.
  Receipt: `.venv/evidence/qa31-repomap-composed-qualification-20261031/terminal-reconciliation-20261101.json`.
  This subsequent task-status edit is documentation only, not a new matrix run.
  Overall G1/release, Q03/Q05 and endurance remain separate open gates.

- **QA31-B instrument controls exposed a private format mismatch:** three old
  Python 3.11 controls pass, then the nested-await control fails because frame
  names are qualified instead of the original expected short name. The run
  stops immediately; no remaining cases or Python 3.12 controls execute. The
  captured stack does contain the actual inner function's file and line.
  Private observer candidate `ad07f1be…d3b6d5` changes only frame labels to
  bounded `co_name`, preserves the assertion and awaits independent review.
  No recovery diagnostic, corpus, soak or new production change has run.

- **2026-10-31 qualification closure requested:** QA31-A prepares corrected,
  isolated CI-baseline environments; QA31-B first investigates retained Q05 boot
  evidence without another corpus attempt. QA31-C final-source/corpus gates and
  QA31-D uninterrupted endurance follow only after prerequisites and a controlled
  execution window. See the current section of `plan.md`. No new qualification
  pass is claimed; seven untracked filmmaking files remain excluded and untouched.

- **QA31-A repaired; QA31-C found a reproducible RepoMap path defect:** both
  fresh baseline environments pass readiness with real editable imports and no
  interpreter override; the missing-backend packaging case passes once. The
  fresh full-source lanes each report 1312 pass/7 fail/5 skip, and plugin lanes
  443 pass/4 fail. Absolute ancestor `.venv` incorrectly excludes every fixture
  file in RepoMap. A two-file fail-first relative-path repair is isolated and
  not integrated yet. Three associated scenario request differences remain
  subject to confirmation. Other source gates including CSS freshness pass;
  no default-matrix retry or new recovery/endurance result is claimed.

- **QA31-C RepoMap repair reviewed and integrated:** exact two-file candidate
  `2278395db3e2fb00aedfad67690915e082f58aa97976dd7467058be2f0704133`
  matches parent bytes and modes after independent source/base verification.
  Four ancestor-path regression cases fail first and pass after the repair;
  the focused module passes 10 tests per baseline. A correctly source-bound
  canonical Python 3.11 scenario group passes all three unchanged goldens and
  retains actual prompt captures containing the workspace Codebase Map.
  Earlier standalone scenario results imported the old editable checkout and
  do not test this candidate; that source-binding error is explicitly retained.
  The worker's test preimage receipt also used its postimage hash; corrected
  actual Git-base preimages are retained separately without replacing it.
  Parent-focused and composed full-source qualification are next, not passed.
  No new commit, push, recovery corpus or endurance result is claimed.

- **2026-10-31 local coding-only checkpoint committed; no push:** `677d5a0`
  preserves 216 pending files. The seven filmmaking-skill files remain original,
  uncommitted and excluded from verified 867-file snapshot `8e30fadb…3aefc`.
  Independent 125-file source review is clear within its stated limits. Fresh
  default pytest: Py312 1319 pass/5 skip; Py311 1318 pass/1 fail/5 skip, with the lone
  packaging failure reporting missing `hatchling.build` in that venv. No retry.
  Plugin tests: 447 pass each; Ruff/doctor/strict UI audit pass each. Environment
  readiness still reports false for the explicit verification PYTHONPATH. The
  earlier five-skill candidate's 39 failures per lane remain retained evidence.
  All owned one-shot jobs/children were cleaned up with absence verified.
  This entry supersedes older current-run/pending statements below, not their
  historical failures or remaining recovery/endurance/native/artifact gates.
  **G1 remains non-green.** See [checkpoint](../docs/testing/CHECKPOINT_2026-10-31.md).

- **Remaining source CI is mixed:** Ruff, import boundaries,44 plugin doctor
  reports and strict UI audit pass per baseline. Plugin suite Py312446pass;
  Py311444pass/2fail in Git-discovery/child-ready fixtures. No retries; retained
  evidence triage only, no assertion/deadline changes. Source867 unchanged.
  Evidence: `.venv/evidence/final3fb-ci-remainder-20261026/`. Earlier full pytest
  passes remain historical facts, not clearance of these separate failing checks.

- **Latest Q05 full attempt fails closed at14/100 accepted pairs:** reviewed
  source3fb/carrier131c trial15 repair exceeds unchanged5s, trace ends at boot
  begin. All14 completed traces pass audit and all60 worker exit receipts report
  complete cleanup. Job ran once/exited2 and was removed; no retry/top-up. Root
  cause remains unknown; retained-evidence investigation only is active. This
  does not supersede or explain the historical9/100 failure. Evidence:
  `.venv/evidence/q05-final3fb-gated-full100-20261026/`.

- **Latest default pytest matrix PASS (not overall G1):** unchanged867-file
  `3fb89fa0…6729` source passes1318 tests/5 skips on each Python3.11.17 and3.12.15,
  with zero failures/errors and unchanged lane ceilings. One supervised invocation
  exited0 and its exact launchd job was removed with absence verified. Four skips
  concern Linux/bubblewrap; one concerns host-specific catalogue generation.
  Other CI steps, recovery, artifacts and endurance remain separate open gates.
  Evidence: `.venv/evidence/g1-one-shot-full-3fb89fa0-20261025/full-matrix-result.json`.

- **Reviewed deadline/policy repairs now locally integrated:** source867 candidate
  `3fb89fa0…` includes the MCP timeout/immediate-stop ownership fix, lazy unused
  sandbox probing, and restrictive fallback WRITE+exec approval parity. The parent
  combined regression batch passes148 tests/3 existing Linux-only skips and Ruff
  per Python version. A fresh full-source matrix is requested through one-shot
  supervision; final full/recovery/artifact/endurance gates remain open.

- **Current final patch in progress:** the unused non-executor sandbox-probe
  correction passes96 focused checks per version (3 existing Linux-only skips).
  Its pre-existing fallback/plugin standard WRITE+exec approval mismatch is being
  closed restrictively before independent review/integration. No goldens or
  operation deadlines change. The integrated MCP deadline/shutdown repair already
  passes all83 MCP kernel/bridge regressions and Ruff per version; final composed
  source/full/artifact qualification remains open.

- **Q03 final-source corpus passes100/100:** all actual builtin-command
  cancellation trials pass on pinned867 `f91ea346…`. Independent PID/birth/ancestry
  checks observe max accepted-cancel-to-reap85.114ms against the unchanged2s bound;
  all100 delayed markers remain absent. Parent rechecks trial/terminal/marker
  facts. This is offline source cancellation evidence, not overall G1/release.
  Q05's reviewed completion checks pass82 controls per version and its fresh smoke
  is valid. Its one full corpus fails closed after9/100 pairs: pair10's repair
  misses the unchanged5s settle bound. All40 partial worker receipts retain clean
  owned-group cleanup/nooverflow; no top-up or Q05 qualification is claimed.

- **Fresh final867 artifacts built, runtime probes pending:** direct wheels and
  sdist-rebuilt wheels pass actual noneditable installed checks on both Python
  versions, including exact MCP server bytes. A clean exact-manifest copy produces
  engine `404af99eb06fecdeb3ed1788df493b896b0d4bd2cfb8e8aeebd7f49dd4b043db`
  (21,568,752 bytes). The new exact binary passes actual MCP inline-image CLI/gate
  and canonical audit/allow-list/shutdown assertions, with source PYTHONPATH removed.
  Prior `4f4d…` ACP/browser/confinement probes do not qualify this new binary.
  Physical-input guard first refused330 generated `.pyc` files; those were kept
  in the tested checkout, not deleted or admitted into the bundle.

- **T18d typed-image delivery repair locally integrated:** the reviewed two-file
  candidate preserves ordered text/inline images under the existing 2024-11-05
  protocol and rejects unsupported/malformed content without payload repr leaks.
  Parent server/export/CLI/audit batch passes 46 tests and Ruff per Python version,
  including actual gate/CLI typed-image audit coverage. Git/background/official-host
  support remains open. The running soak source is unchanged; this later runtime
  repair still needs composed full-source and refreshed artifact qualification.

- **2026-10-25 latest full qualification (supersedes older status below):** frozen
  reviewed867-file candidate `f91ea346…` completes1280 passed/5 Linux-only skips
  on Python3.12. One instrumented3.11 diagnostic also passes1280/5skips, but its
  predeclared uninstrumented confirmation reports1278 passed/2 failed/5 skips:
  MCP cancellation355ms against200ms and a headless MCP write reply missing1s.
  Earlier Git/DOM failures pass that confirmation; no earlier cause is declared
  fixed. The original headless question `<1s` test passes current lanes. Targeted
  MCP phase measurement is active; no further broad retry loop is authorized.
  G1 remains non-green. Source paths, modes and bytes remain unchanged per lane.
  Main subsequently gained seven filmmaking-skill files; they remain untouched
  and outside this reviewed qualification candidate.
- **Current exact-artifact checks pass:** engine `4f4d1bb5…` reads all four original
  skills with both source checkouts unreadable, completes offline ACPv1 text and
  one-time approval/rejection/permission-cancellation exchanges, and passes the
  unwrapped inside/outside file-read canary with CLI exit0. Actual Chromium/HTTP
  checks report44 plugins/four core panels/matching assets, a persisted composer
  turn, unchanged read-only session metadata and clean0.342s shutdown. Retained
  traces/logs are authentication-redacted. These are not native WebKit/editor,
  network/write confinement, process-cancellation or complete release results.
- **Local app assembly complete, runtime tier still open:** the Swift launcher
  builds with an explicit SDK and the existing macOS12 target; ad-hoc deep/strict
  signature verification succeeds without changing the tested engine bytes.
  The `.app` was not installed or launched. Shared Foundation state and the lack
  of an existing isolated guest/account leave native WebKit/editor checks unrun.
- **T29 operational attempt failed startup; endurance still unproven:** the first
  real soak invocation exited 1 after 2.759 seconds because the verifier rejected
  legitimate namespace imports without `__file__`. The isolated two-file repair
  passes 51 controls per Python version, including an actual first tool turn;
  independent review cleared its exact candidate, and byte-identical integration
  passes all 51 controls and Ruff per version. A fresh 7,200-second/120-turn source
  invocation on `7e80c0ee…` was later found interrupted at 91 completed-turn
  records: its controller is gone and no terminal receipt or resource report
  exists. The Mac did not reboot; interruption cause is unknown. No endurance
  pass is claimed, and the incomplete run is archived.
  Q03's false-qualification repair passes 40 controls per version and clears
  independent source review; its fresh smoke is queued behind the soak. Q05's
  ownership-handoff repair passes 43 controls per version and clears independent
  source review; unknown or surviving group members do not count as complete
  cleanup. Actual fresh final-source smokes then expose two setup gaps: Q03 does
  not handle its fixture command's legitimate approval; Q05's directory creator
  violates its own private-mode check. Both harness repairs are isolated, with
  no policy/permission guard weakening. Neither production corpus has run. The
  final 867-file source matrix and the single confirmation have completed with
  the results above. Q05 completion-evaluator tightening passes82 controls per
  Python version and awaits affected review before its fresh smoke/full corpus.
  Prior startup, timing and harness failures remain retained.

- [x] **2026-10-24 dependency repair locally integrated:** exact reviewed parser
  override removes the three moderate npm audit entries. Fresh worker and parent
  installs audit clean, preserve CSS bytes and pass focused UI controls. The parser
  override crosses upstream `^6` ranges; no general API-compatibility or release
  claim follows. Existing parent dependencies were not forcibly replaced. T27's
  composed full/artifact qualification remains open; see DEPENDENCY_POLICY.md.
- [ ] **Current G1 qualification remains non-green:** on the preceding863-file
  `e90a2323…` snapshot, Python3.11 reports1216 passed/5 Linux-only skips and
  Python3.12 reports1213 passed/3 failed/5 Linux-only skips. The unchanged three
  failing tests then pass in isolation on both versions, and25 original plus
  supplemental probes pass per version. These reruns do not establish the earlier
  failures' cause or erase them. The newer dependency/doc composition needs a fresh
  complete-source pin and full run; no timing assertion or golden was weakened.
- [x] **Bounded live OpenRouter source smoke observed:** the explicit free model
  streams a CLI reply, reads a synthetic file, receives canonical tool results,
  executes a narrowly approved write and recalls the token after runtime reload.
  Three denied writes in the retained failed fixture created no output; that
  fixture's relative-versus-canonical approval-path check is corrected before
  explicit continuation. Eight total product completion calls stay within the
  bound; API account usage shows zero increase. This is CLI/Commands/core evidence,
  not native UI/editor/media/all-feature qualification or accurate pricing for
  every model. No key is persisted by the driver or used for further calls.
- [ ] **T29 runner is not integrated or qualified:** its first two-file candidate
  passes18 fast tests per version, but independent review finds worker-root/output
  coupling, unsafe direct worker entry, missing tool-success checks and missing
  post-run source verification. Repairs are isolated; the real7200-second mixed
  soak and current-candidate100-cancel/100-restart gates have not run.
- [ ] **Fresh artifact harness remains under repair:** current-source browser
  binding, complete input inventories and isolated process environments are
  prepared; token-free retained browser evidence still needs review. No fresh
  complete-product native binary or browser result is inferred from this work.

- [x] **2026-10-24 offline ACP/reconciliation integration milestone:** the complete
  reviewed v1 SDK slice and Git prerequisite are integrated after exact parent
  destination checks. The unchanged77-case gate passes on both Python versions in
  the owner-confirmed quiet window; the real parent then passes414 combined bridge/
  UI/broker/privacy/session/checkpoint/resource cases per version. Earlier timing
  failures remain retained, not attributed solely to load. Real-editor, fresh
  complete native/wheel artifacts, full-suite/G1 and soak gates remain separate.
  See docs/testing/ACP_COMPATIBILITY.md and docs/testing/G1_GIT_LAUNCHER.md.

- [x] **R22 event-barrier ordering locally repaired:** exact reviewed two-file
  candidate is integrated after cancelled browser work is confirmed inactive.
  Parent52-case bus/supplemental and192-case combined bridge/privacy/UI groups
  pass on both Python versions, preserving all existing assertions and limits.
  Earlier browser evidence remains bound to its old backend; remaining stages
  require the new bus identity. No full-G1/browser/artifact qualification implied.
- [x] **2026-10-24 cancellation recovery:** no implementation changes beyond the
  declared input seed were found in the cancelled availability worker; browser
  continuation never created its assigned evidence directory and no matching
  owned process is running. Partial sources/logs are preserved. Missing encoding
  modules in both temporary Python bases are recovered with exact baseline
  versions in durable ignored project storage; new192-case results are refreshed.

- [x] **R21 / T19–T23 shared prerequisite — locally repaired.** EventBus no longer
  merges text/thinking/tool output across session IDs when call/turn IDs coincide.
 24 canary/order negatives fail first;33 controls pass per Python version. Exact
  source/patch independently verified. This does not close UI/ACP end-to-end gates.

- [x] **R20 / T25 security prerequisite — locally repaired.** Shared no-follow
  descriptor reads block outside skill content in both view and catalogue consumers.
  Default path/swap/alias/type/bounds controls pass:51 Python 3.12 skill/search
  checks and32 Python 3.11 skill/ledger checks. Independent review verifies exact
  snapshot and patch hash with no actionable issue. Original R1–R19 stay intact;
  supplemental findings remain required for release. T25 packaging/T26 artifacts
  and global qualification remain open. See R20 evidence.

- [x] **Zide recommendations documented and added to the development workflow.**
  Root AGENTS.md points agents to tasks/WORKFLOW.md. The cited research note owns
  ZIDE-01–04, acceptance and roadmap mappings. Exact-candidate review and parent
  destination rechecks are process requirements now, not shipped runtime features.
- [ ] **Zide runtime adoption remains pending.** T23/T24 → T37 own scoped status
  and attention UX; T38 owns one canonical task/worktree/child path. ZIDE-04 adds
  a focused stale-Git-approval security contract before Git mutation admission;
  T05's completed cancellation repair is not relabeled as implementing it.

- [x] **T12 — OpenAI text continuations locally verified.** Canonical USER-carried
  results preserve IDs/errors on the actual second HTTP request. Fresh store/log
  replay matches; strict peer rejects an intentionally missing result. Nine checks
  pass on each Python version, R02 passes; Luna source review found no actionable
  issue. Media/live-vendor/global qualification remains pending.
- [x] **T14/T15 — Luna candidates integrated and independently tested by parent.**
  Real Anthropic SSE error/EOF/breaker controls, cumulative usage and deterministic
  byte fragmentation pass. Gemini connection/read transport errors reach the actual
  runner with valid SDK kinds. Combined subset: 46 passed on each Python version;
  original R09/R14/R02 pass. Luna source reviews found no actionable issue. See
  T14/T15 evidence; global/live qualification remains incomplete.
- [x] **T13/T16 — Media/XML candidates composed, reviewed and locally verified.**
  Parent preserved T14/T15 changes, closed malformed-authority/effective-model
  findings, preserved the builder call contract and fixed XML text/argument edge
  cases. Exact scoped snapshots and patch hashes bind acceptance; final combined
  subset: 105 passes on each Python version, originals R06/R10 pass. Live/global/
  artifact qualification remains incomplete. See T13/T16 evidence.
- [x] **T17 — MCP export restriction locally integrated and verified.** Both CLI
  and plugin use one canonical-first resolver; absent/empty exports deny all and
  malformed allowlists fail closed. Six actual CLI controls plus plugin/positive
  fixture cases total 18 passes on each Python version; R13 passes. Exact candidate
  digest source-reviewed; T18 execution and ZIDE-04 approvals remain separate.
- [x] **T21 — Late MCP registration locally verified.** One condition routes
  successful recovery through the existing plugin catalogue callback. Actual
  registry/gate invocation, unchanged reconnect, replacement/removal and disconnected
  errors pass;14 parent client checks pass per version and R11 passes. Exact
  candidate source-reviewed. Initial HTTP-handshake caveat remains unproven.
- [x] **T18a — Actual CLI read mediation locally verified (partial task).** Real
  gate/session/principal, validation/redaction and denial controls pass, including
  strict one-second headless RPC rejection. R04 passes;35 combined MCP checks pass
  on Python 3.12, separate21 CLI/server +14 client runs pass on Python 3.11.
- [x] **T18c audit/read-ledger slice locally accepted:** canonical external call/result
  records, real successful-read ledger, privacy copies and uncertainty fencing are
  integrated.64 focused checks pass per Python version; exact source/patch and all
  review follow-ups are independently reviewed. See T18C evidence and its limits.
- [ ] **T18 remains open:** unqualified MCP-server nontext/Git/background/host
  cases. T18b cancellation is
  locally accepted; T22 client media is a separate completed local slice.
- [x] **T22 — MCP tool media locally verified.** Image-only/mixed results survive
  real peer/gate/store and fresh-log replay into Anthropic adapter requests, using
  MockTransport rather than socket/vendor calls. Both allocation/empty-result
  review findings fixed;67 checks pass per version, R12 passes. Exact parent
  source/patch re-reviewed and accepted. Broader release qualification remains open.
- [x] **T18b output-bound cancellation locally accepted:** every flush is bounded;
  EOF shares its wait/flush deadline and stalled output is aborted before cleanup.
  Parent95 combined checks pass per version; exact source/patch re-review passes.
  Worker initial combined timeout remains an unproven caveat, not a claimed fix.
- [x] **T25 resource repair locally verified:** parent direct and sdist-rebuilt
  wheels contain four original skills,44 manifests and83 checked resources. Both
  Python versions build/install/run the probes;3.11 uses an offline build-tool
  overlay, not a fresh dev install. Dev prerequisites declared and tested. Exact
  source/patch reviewed; six hashed resource-test artifacts retained. R19 passes;
  16 repaired original probes pass together on3.12. Final release remains open.
- [x] **T19 internal binding locally accepted:**32 focused
  checks now pass per Python version after the accepted headless-import input
  amendment, including unchanged R05 and the real stdio peer. The strict2s reply
  deadline is unchanged; the earlier3.11 initialize timeout is preserved. Delivery
  bounds and fail-closed cleanup are repaired; oversized source strings no longer
  require unbounded scanning, and shared environment-secret discovery is wired.
  The test peer drains both pipes
  under its unchanged close deadline. Exact isolated source review is clear;
  standalone bytes and the ACP function are now integrated, preserving every other
  parent CLI function. A combined115-case ACP/MCP/privacy/CLI matrix passes per
  Python version. Independent exact shared-CLI composition review is clear and
  parent source/patch/JUnit rechecks pass. Official ACPv1/editor interoperability
  remains T20, not inferred from this internal legacy0.1 binding.
  Earlier9-pass/3-timeout and120-second diagnostic evidence is retained; no sole
  cause or full qualification is claimed.
- [x] **T23 scoped event/action behavior locally verified:** reviewed UI bytes,
  four required Node22 semantic cases and35 frontend checks per Python version.
  Actual Chromium continuation verifies A cancellation preserves B approval/busy,
  native B approval writes only B marker, and global settings update. Browser runs
  span interruptions; visual approval-title and stale-history follow-ups remainT24.
- [x] **G1 startup prerequisite locally accepted:** unused Anthropic/Gemini/Ollama
  transports are lazy;10 lifecycle and95 provider regressions pass per version.
  Historical strict headless1s failed311; a fresh unchanged check on2026-10-24
  now passes both versions in the recovered current environment. Earlier failures
  remain retained, and no sole-cause/performance or full-G1 claim is made.
- [x] **G1 headless import prerequisite locally accepted:** RPC startup no
  longer eagerly imports the interactive CLI/Markdown renderer.19 CLI/import
  controls and64 MCP controls pass per Python version in separate groups. Actual
  import blockers establish no unused renderer initialization; no ACP latency
  performance percentage or full G1 qualification is claimed. Independent source
  review is clear after the inherited mock-script test-isolation fix.
- [ ] **T24 recovered candidate review follow-ups in progress:** worker DNS failure
  leaves no completed-task receipt. Parent preserves all five partial files and
  verifies720 unchanged non-allowed seed files. Three additional identity cases
  fail first and are repaired without content matching or reusing keyed bubbles.
  Node22:16 checks pass;11 focused Python controls per version include unchanged
  R07, now green in isolation. No parent UI integration, real-browser/reconnect or
  full R07/T24 closure is claimed.
  Independent source review finds three remaining P2 gaps: canonical timeline
  ordering, snapshots racing unkeyed assistant bubbles, and reconnect replay gaps.
  The bounded follow-up candidate addresses those gaps and passes19 Node22 cases;
  the parent strengthens the required wrapper floor to19 and reruns11 focused
  controls per Python version. Re-review finds two further P2 cases: an older
  snapshot must not adopt the active newer turn's stream, and tool-card ownership
  must follow the assistant record even when the real producer emits completion
  before tool execution. The follow-up has21 passing Node22 cases, and parent
  reruns11 focused controls per Python version with the wrapper floor strengthened
  to21. Misplaced worker logs are preserved by hash; its missing-pytest attempts
  are setup failures, not passes, and the original bases were always present.
  Exact-candidate source review is clear for the two targeted fixes. The UI is now
  locally integrated; an unchanged existing static-contract assertion exposed a
  payload-expression mismatch, fixed in source while keeping captured reply guards.
  That one-line follow-up is source-reviewed and25 focused parent checks pass per
  version. A broader3.12 frontend run was90 pass/1 failure before that fix; the3.11
  broad run was incomplete at240s under contention. Neither is a full-green claim.
  Actual browser stage1 stale-A-after-B selection passes. Later stages stop on a
  fixture classifier treating a user-carried tool result as an empty prompt, not
  a product UI failure. The cancelled continuation never started. On2026-10-24
  parent fixture-only real-Message regression fails first, then the classifier is
  corrected. Stage2's runner then stalls on an invented metadata.call_id result
  field; saved real records already contain the successful result and follow-up.
  Parent fixture-predicate regression fails first, then reads canonical tool_calls
  and tool_results IDs. The corrected actual Stage2 passes ordering, distinct
  repeated prompts and owned tool-card/output reconciliation with cleanup0/ports
  closed. Screenshot inspection exposes clipped cards; a one-rule CSS repair is
  source-reviewed with real component geometry red/green and25 parent checks per
  version (required Node22 cases). Stage3 then reproduces two visible A answers
  after a held completion and actual tool boundary. Source tool.call finalizes
  the unkeyed stream before snapshot adoption; an exact-sequence regression and
  real-call-ID orphan binding repair produces24 passing Node cases. Review finds
  a tool-only next-message association gap and lost Markdown formatting on a
  finalized orphan. Both now have genuine fail-first regressions and a source-clear
  worker candidate with26 Node cases and34 related controls per Python version.
  After confirmed fixture cleanup, the exact reviewed6398 candidate is integrated;
  25 focused parent checks pass per Python version with26 required Node cases.
  The complete tests/frontends group subsequently passes85 cases per version;
  this supersedes earlier incomplete frontend runs for the current UI snapshot,
  not the full-suite or pending kernel/protocol integration gates.
  The exact failed Stage3 real-browser journey now passes, with native held A
  completion, busy snapshot, one canonical A, one owned tool card and distinct B
  continuation; clean child exit and closed ports are verified. Stage4 passes native approval, same pending ID after refresh,
  automatic canonical reload on a controlled absent replay checkpoint, correct B
  marker and normal continuation counts. Its approved screenshot is inspected;
  cards fit and expose APPROVED plus accessible resolution text. Stage5 stops on
  a harness screenshot binding error. Its first resume then rejects expected old-A
  cancellation history before browser startup; that failed fixture has stopped.
  A later Stage5 classifier invents a notice metadata.code; a saved-record
  regression fails first, then uses the actual notice source/content. Stage5 now
  passes on6398: new A is policy-denied, B's historical user approval reloads idle,
  no new B model request occurs, and original A/B transcripts remain unchanged.
  Parent inspects the settled screenshot; child exits0 and both ports close.
  All five stages are complete across explicitly bound snapshots, not one
  uninterrupted latest-tree journey. See docs/testing/T24_TRANSCRIPT_RECONCILIATION.md.
- [x] **T24 persisted-session metadata follow-up locally repaired:** a fresh manager's
  read previously changed original created_at and rewrote session.json.
  The browser recovery fixture exposed the metadata delta; an actual-manager
  deterministic restart probe separately reproduces1234.5→9876.5 without any turn
  re-execution. The first isolated candidate passes6 new and3 existing controls
  per version. Source review finds two P2 gaps: listing bypasses finite-
  timestamp validation, and non-object JSON/root or nested metadata can crash or
  violate response shape. That rejected candidate is preserved. The same-reader
  follow-up passes31 new cases per version, clears exact-source re-review and is
  integrated; parent71-case metadata/lifecycle/Commands/log/UI groups pass per
  version. Earlier fixture records remain unchanged; metadata mutation is not
  described as tool replay. Original native candidate c651 predates this repair;
  the later190b rebuild includes it and verifies read-only metadata preservation.
- [ ] **T26 artifact qualification started:** reviewed mandatory ui.toml recipe
  and4 new/default plus original R18 checks pass per version. Actual intermediate
  PyInstaller6.22.3 engine builds22.3MB in110s with852-file inputs unchanged. Copied
  outside source with checkout reads denied, it reads all4 bundled skills by hash,
  loads44 plugins/4 core panels and serves matching assets; a Chromium mock turn
  against that exact engine passes. Shutdown exceeds10s and requires a forced owned-group stop. An
  independent actual-source HTTP probe isolates disconnected SSE waiting for its
  heartbeat: no-SSE stop0.001s, disconnected-SSE fails1s. The bounded owned-stream
  shutdown repair is active, not a timeout increase. No qualified release claimed.
  See docs/testing/MACOS_ARTIFACT.md for artifact identity and isolation limits.
  The actual binary's no-SSE control exits0 and closes both ports in0.291s;
  withholding only its owned extracted ui.toml trips the real resource gate,
  followed by clean0.813s shutdown. The first stream fix has cleanup-exception and
  setup/replay error-propagation review follow-ups. The final75d7 source is clear
  and integrated; parent138-case frontend/metadata/lifecycle/log/recipe/original
  groups pass per version. The original source disconnected-SSE reproduction now
  stops in0.001s without a wake event. A separate native build/replay follows; older
  c651 remains shutdown-failed and its10-second bound is unchanged.
  The rebuilt190b candidate now passes real four-skill/44-plugin/core-panel/asset/
  Chromium checks, preserves read-only metadata, and exits0 after browser use in
  0.948s with both ports closed and daemon record removed. Its855-file input is
  stable through build. It still excludes pending ACP/Git integration. Separate
  unwrapped native sandbox-status attempt hits30s; one bounded file-backed
  diagnostic subsequently exits0 in4.94s and reports Seatbelt available/partial.
  The earlier cause remains unknown. Actual native file-read canary controls are
  active; availability alone is not counted as enforcement qualification.
- [ ] **T20/T24 remain separate:** official ACP interoperability and stale-history
  reconciliation are not established by internal binding or event filtering.
  Owner-authorized parallel T20 planning now uses a distinct724-file seed from
  the held T19 source, not HEAD or an assumed latest-parent runtime. It verifies
  one official specification/client release and a bounded compatibility plan;
  that planning lane itself performed no client execution or implementation.
  Subsequent real SDK0.12.1 subprocess execution reproduces the legacy string0.1
  versus integer-version failure, with captured wire data and clean child exit.
  T20a negotiation/session implementation now runs in an isolated branch from
  accepted checkpoint469de56; its partial v1 path must not be integrated before
  the complete turn/permission/cancel slice is ready. SDK/client and editor tiers
  remain distinct; no editor was found in the two checked standard locations.
  The T20a candidate has5 focused SDK/raw-boundary passes on its own3.12
  environment;3.11 and legacy matrices were initially deferred. The NUL-cwd ValueError and
  connection-drop finding is reproduced and fixed; exact-source re-review is
  clear, with808 non-allowed seed files unchanged. It remains an unintegrated
  session-only intermediate, not a complete official ACP compatibility claim.
  After runtime recovery, fresh37-case legacy-plus-SDK groups pass on3.12 and3.11
  in worker-owned environments; the earlier deferral is now superseded for this
  exact intermediate. An isolated text/resource-link/update/cancel step is now
  implemented and source-reviewed with10 official SDK seam checks per version.
  Fresh42-case legacy-plus-SDK groups pass on both versions for the exact
  intermediate; it remains unintegrated and lacks bidirectional permissions.
  Bidirectional permission mapping now has an isolated candidate reporting50
  focused passes per version and is under exact-source review. Parent dispatch
  shorthand conflicts with its captured allowlist, splitting nine new controls
  into newtest_acp_interop.py, which default pytest discovery does not select.
  The exact five-file candidate is preserved; integration is blocked pending
  assertion-preserving consolidation into the authorized default test module,
  refreshed review and qualification including unchanged original R05. Independent
  source review also finds a P1 deadline race: forwarding starts a new budget and
  the broker's resolution claim does not check original expiry. The accepted
  follow-up contract adds an internal original monotonic deadline, shared locked
  expiry admission and adapter receipt checks with deterministic boundary tests.
  This expands the repair scope explicitly, not through an invented worker approval.
  The six-file follow-up is source-clear: original monotonic deadline admission
  and all19 default-discovered interop cases plus7 deadline controls pass in worker
  groups. Fresh parent-owned serial candidate qualification including original R05
  passes77 cases on3.11, but3.12 is75 pass/2 unchanged approval-event timeouts.
  Saved traces put those failures in checkpoint prompt hooks before approval
  handling. The focused read-only diagnosis passes3 cases per version but does not
  clear the gate. A second uninstrumented combined run again passes77 on3.11;
  3.12 is73 pass/3 timeouts/1 setup timeout around cold/post-turn checkpoint work.
  Paired same-version Git controls show lower observed initialization/approval
  waits through the real installed Git binary rather than Apple's launcher. A
  narrow macOS resolver/cache repair subsequently clears source/base re-review,
  passes36 parent checkpoint controls per version and is integrated without
  disabling checkpoints, changing architecture or lowering deadlines. A composed
  run still has one cold-start timeout on3.12; corrected outside-source fixtures
  still show intermittent startup misses, and a qualified targeted diagnostic
  passes without establishing their cause. After the owner confirms a quiet host,
  the unchanged full77-case group passes per version. The complete reviewed six-
  file ACP slice is then integrated with its test-only SDK pin and actual original-
  deadline broker checks;414 broad parent integration cases pass per version.
  This is offline SDK scope, not editor, full-G1 or current-artifact qualification.
- [x] **T20 kernel availability prerequisite locally verified:** kind metadata is
  immutable, legacy defaults retain both kinds and broker checks the requested
  kind. Source review is clear;60 pure worker and202 combined parent cases pass
  per Python version in the original prerequisite groups. The v1 approval-only
  registration is now an active integrated consumer and its unsupported-question
  behavior is verified in the SDK/parent groups. This does not establish the
  separately required editor or global qualification tier.
- [x] **T3 Code source comparison completed:** Luna inspected upstream commit
  `22840ab943b8418744b48223411e46ba4fdcef1a` (MIT). Keep the kernel; consider bounded
  replay/snapshot reconciliation within T24 and explicit base/worktree launch facts
  within T38. These are advisory design references, not implemented features or
  roadmap expansion. No upstream install, app use, tests or dependency-license
  audit was performed; no external code/assets were adopted.

- [ ] **G1 — Full safety/recovery qualification remains non-green.** Fresh
  uninterrupted 3.12 run: 831 passed/2 failed/5 skipped, identical tree before
  and after. Three narrow follow-ups remove redundant unchanged-checkpoint diffs,
  distinguish reaping from output drain, and cache static validation metadata.
  Both failed scenarios and 51 selected checks pass on each Python version.
  The unchanged strict headless latency still fails under load; no full rerun
  after these changes is claimed. See G1 evidence. Continue original T12 defects,
  not feature promotion; no assertion/golden/deadline weakened.

- [x] **T11 — Wrapper and native macOS acceptance locally verified.** Final
  subsets: 27 passed on each Python version, three Linux-only skips. Actual
  confined nested pwd/relative writes and outside-file denial pass; R08 passes.
  The earlier host-unavailability diagnosis was wrong: the loader needed exact
  root-directory read access, not a permission popup or broader descendant reads.
  See T11 evidence. No security setting, paid CI, commit/push or remote change.
  Global/Linux qualification remains incomplete; T12 follows.

- [ ] **T01 — Execution finished; qualification blocked by B01–B04.** Clean
  Python 3.11/3.12 + Node 22 established; historical four-lane remote evidence
  retained. Full runs: 3.11 **712 passed / 7 failed / 5 skipped**; 3.12 **715
  passed / 4 failed / 5 skipped**; no setup errors. All failures/skips have an
  observed classification and owner in `docs/testing/BASELINE.md`. Failed scenario
  subsets pass in isolation (6/6 and 3/3), not as a full-suite fix. Strict headless
  latency remains red; no threshold lowered. No new Linux/native claim.
- [x] **T02 — Preserve fail-first evidence.** All 19 explicit probes ran red for
  their documented reasons, no setup errors; index/fixtures and stronger repair
  acceptance tiers are in `docs/testing/REVIEW_REGRESSIONS.md` and JSON index.
- [x] **T03 — Establish acceptance contracts.** BC-01–BC-11, numerical safety/
  recovery gates, acknowledgement/power-loss limits, provider support semantics
  and required verification matrix documented. These requirements are not
  assertions that current implementations pass or that release is approved.

The T01 full-run processes finished. Their failures remain open; isolated passes
are not a global fix. Commit/push/publication have not been requested.

- [x] **T04 — Local repair verified; global qualification remains blocked.** Search uses no-follow
  descriptor-relative traversal and regular-file reads; no executor fallback.
  Locked-gate controls, internal/external link policy, deterministic leaf/parent/
  root swaps, ignored roots, unreadable entries, FIFO and descriptor cleanup are
  covered. Targeted 3.12: 109 passed / 1 documented catalogue skip; 3.11: 96
  passed. Corrected R1 probe is red on original code and green after repair;
  isolated installed wheel also passes. Independent static review found no
  substantive issue. The full runner disappeared before its final summary; the
  remaining 34 nodes were completed in foreground batches. Partitioned coverage
  of 744 collected tests is 737 passed / 2 failed / 5 skipped, NOT a clean
  uninterrupted suite. Both failed scenarios pass in isolation, not as fixes.
  See `docs/testing/T04_SEARCH_CONFINEMENT.md` for exact limits and provenance.
  R2–R19 remain open. Subsequent T05 work is recorded separately below.

- [x] **T05 — Local repair verified after independent review and follow-up fixes.**
  Pending approvals/questions honor turn cancellation, refuse late answers and
  clean up through request persistence; queued handlers recheck cancellation.
  Earlier claimed decisions are persisted truthfully without authorizing a
  cancelled handler. Final reviewed broad subset: 65 passed on 3.12, including
  all 19 approval/control cases; the 19 cases plus docs/contracts: 25 passed on
  3.11. Five scenarios and 200 deterministic repeated-cancellation cases pass.
  Two reviewer findings and a callback-order race were reproduced and repaired;
  final source-only re-review found no remaining issue in that delta.
  The known headless latency assertion still
  fails; a bounded broader attempt was incomplete. R3 remains partial because
  T05's recorded active-command probe was still red. Subsequent T06 progress and
  its remaining ownership limitation are recorded below. No commit or push.

- [x] **T06a/T06b — Local lifecycle verified; global/native qualification blocked.** Active
  process/STOP/timeout/snapshot cleanup is implemented. Owner approved a supervisor;
  isolated stdlib/private-pipe control replaces numeric identity guesses and the
  shell prototype. Final compatibility: 88 passed/3 native skips; focused 100
  confirmed stops and wheel/one-file smokes pass. The builtin-route 100-trial
  setup attempt is inconclusive, not hidden. See final T06 evidence; historical
  checkpoint remains preserved. No commit or push. T07 follows sequentially.

- [x] **T10 — Durable compaction locally verified in slices.** Summary append
  precedes completion; coverage-aware projection survives restart, preserves
  normal prompts/unresolved calls, and marks oversized text-prompt compression.
  Usage/cap and queued-wakeup regressions repaired. Broader 123-pass subsets on
  each Python version precede final event/draft changes; final event subset is
  55 passed/1 platform skip, final compaction checks are 21 passed (3.12) and
  30 passed including contract/unit controls (3.11). R15 and all five locally
  repaired original probes pass. Later interrupted broad runs are not green.
  See T10 evidence; native/global qualification remains blocked. T11 follows.

- [x] **T09 — Local compaction contract slice verified.** Current ordered hook
  dispatch and valid summarizer requests work through real bootstrap and a strict
  local HTTP/SSE peer. Seven failure controls reject false completion; total
  summary stream uses the existing request deadline. Final subsets: 34 passed on
  each supported Python version. R15 remains open for T10 summary persistence.

- [x] **T08 — Local dangling-call repair verified.** All unanswered IDs receive
  durable synthetic errors via their actual append receipts. Reconstructed stores
  finish interrupted repairs without duplicates. Final subsets: 44 passed on each
  supported Python version; original R17 passes. See T08 evidence; T09 follows.

- [x] **T07a/T07b — Local recovery/receipt verification complete.** Unterminated
  tails preserve bytes, counter reserves both logs' valid indices, malformed
  rows do not erase prefix, cancelled writes retain lock/cache agreement, and
  emitters use actual append receipts. Final targeted subsets: 62 passed on
  each Python version; 100 actual writer-kill/restarts lost zero acknowledged
  records. R16 passes; source review closed its decoder finding. Global/native
  qualification remains non-green. T08 follows; no commit/push.

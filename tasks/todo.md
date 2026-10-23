# Active task status

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
  Strict headless1s passes312 but fails311; G1 remains non-green.
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
  Exact-candidate source review is clear for the two targeted fixes; no parent UI
  integration/browser acceptance. Known EventBus cross-barrier delta coalescing is
  outside this UI candidate and is not claimed repaired or wire-order qualified.
- [ ] **T20/T24 remain separate:** official ACP interoperability and stale-history
  reconciliation are not established by internal binding or event filtering.
  Owner-authorized parallel T20 planning now uses a distinct724-file seed from
  the held T19 source, not HEAD or an assumed latest-parent runtime. It verifies
  one official specification/client release and a bounded compatibility plan;
  no protocol implementation, client execution or interoperability pass is claimed.
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

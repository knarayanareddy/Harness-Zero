# R20 — locked view_skill reads an outside SKILL.md

Severity: P1. Status: **locally repaired and source-reviewed; global/artifact qualification blocked** (BC-01/Q-01).
Owner: T25 security prerequisite, before bundling/distributing builtin skills.
This is additional to the original R1–R19 audit, not a renamed original finding.

## Measured impact

On the recorded baseline, through bootstrap_kernel with `kernel.security.preset=locked`
and the actual PolicyGate, `view_skill` returns a distinct outside-file canary when skill_name
is an absolute outside directory, a relative traversal, or a normal name whose
workspace skill directory is an outward symlink. The marker differs from the
request string, ruling out an echoed-query oracle. Only disposable synthetic
data was read. No real credential, installed-app exploitation or remote attack
is claimed by this probe.

The baseline source accepts skill_name as an identifier string, but `find_skill_file`
joins it directly into builtin/workspace roots and follows existence checks
(`harness/plugins/tools/skills/tools.py:54–64`). The handler then opens the
returned pathname (`:78–83`) outside capability-gated workspace file methods.
The READ classification does not turn that arbitrary pathname into an approved
resource. The tested private target is specifically an outside SKILL.md; this
report does not generalize untested path forms into arbitrary-file exploitation.

## Reproduction/evidence

```sh
python -m pytest -q review/supplemental/test_skill_confinement.py --tb=short
```

- Current parent: **3 failed**, all outside canaries visible;
  `.venv/evidence/r20-skill-confinement-red.{log,xml}`.
- Reviewed gate/skill-reader baseline bytes: **3 failed**;
  `r20-skill-confinement-reviewed-base.xml`. Imports were verified against the
  HEAD-based T21 worktree, and both gate.py and skills/tools.py match reviewed
  SHA bfa52425c1ff37536f31dc74df2ed66dcd953e58 byte-for-byte. That worktree also
  contains an unrelated MCP-client condition edit, not used by this probe; it is
  not described as an entirely clean baseline checkout.
- `t25-inspection-base/manifest.json` records the untouched resource/skill-reader
  state before further work. Repair evidence follows below; these red results
  remain baseline evidence, not the current candidate's expected behavior.

## Bounded parent repair (locally accepted)

`harness/sdk/skill_resources.py` now owns the shared roots and content reads.
It rejects path-like identifiers and acquires every root component, skill folder
and leaf relative to pinned descriptors with no-follow opens. Nonblocking leaf
opens plus `fstat` refuse FIFOs/directories, and an incremental read enforces a
1 MiB per-file limit, including files which grow after `fstat`. Unsupported
descriptor platforms fail closed. Workspace roots are not resolved through
symlinks; trusted builtin selection retains the previous source-checkout root.

ViewSkillHandler and catalogue discovery consume already-read content. Neither
reopens a returned display path. Exact-name, case/underscore aliases and builtin
precedence remain. T04 search was deliberately not refactored: its existing
descriptor pattern informs this reader without broadening the verified search
repair. No CWD fallback, network fetch, new settings or capabilities are added.

- Default regression `tests/kernel/test_skill_resource_confinement.py` first
  reproduces **five** locked-gate path forms. After only view was fixed, its
  catalogue assertions still fail for folder/leaf/root links
  (`r20-catalogue-red.xml`): that metadata leak is now measured too.
- Both consumers fixed: **26 default skill checks pass on Python 3.12** before
  final combined runs, including deterministic pre-open workspace/root/folder/
  leaf swaps and post-pin root/folder swaps, aliases, valid skills, file-type/
  byte/UTF-8 refusal and descriptor cleanup. Synthetic swap hooks verify the
  parent descriptor identity and that the intended boundary was exercised.
- Final Python 3.11 skills/supplemental/ledger subset: **32 passed**
  (`r20-final-py311.xml`). Python 3.12 including existing search controls:
  **51 passed** (`r20-search-controls-py312.xml`). Ruff and diff whitespace pass.
- `r20-parent-base/manifest.json` captures the actual scoped base; snapshot
  `5cfae96f5bfa037ed14762e099aabf7a6f74dc98d097761aba6fa87be72a1d7a`
  passed Luna independent source review with no actionable issue. The reviewer
  ran the read-only snapshot verifier and independently checked complete patch
  SHA `15492924e5d3d859f79a490f9c1988985953a8bdb2653adc74c3379e52f7c249`;
  the reviewer did not execute tests. Parent rechecked both before acceptance;
  see `r20-acceptance-receipt.json`.

An initial combined invocation collided with the diagnostic module's basename
and failed collection; the new default module was renamed before final runs.
That attempt is not counted as a functional pass. This is not a wheel/frozen or
full-suite qualification. The per-file limit is not a total catalogue memory
budget. Installed resource layout and platform qualification remain T25/T26.

## Required repair acceptance

- [x] Skill identifiers cannot select absolute, parent-traversal or multi-part paths.
- [x] Shared bounded-authority reading for builtin and workspace skill roots uses
  acquired descriptors/no-follow traversal, not resolve/check-then-read paths.
- [x] Folder/leaf/ancestor symlinks and deterministic swaps cannot expose private
  canaries; legitimate builtin and workspace skills and identifier aliases work.
- [x] Catalogue metadata discovery uses the same safe reader; its folder/leaf/root
  canaries reproduce before that consumer is repaired, then pass.
- [x] Required default regressions cover original path forms and additional controls.
- [ ] Installed wheel/frozen resource probes pass without relying on checkout paths.

Original R1–R19 probes and task IDs remain unchanged. Supplemental findings have
their own ledger section and must pass release qualification; the source audit
was never proof that no further defects could exist.

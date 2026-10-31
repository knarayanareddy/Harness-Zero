# Dependency qualification — T27

Status: the bounded npm repair is locally integrated, source-reviewed and
compatibility-tested. Current full-suite and rebuilt-artifact qualification remain
open. This is not a release or universal vulnerability-free claim.

## Audited scope and smallest repair

The manifest has two npm development dependencies, `jsdom@24.1.3` and
`tailwindcss@3.4.17`, and no npm runtime dependencies. The original lock audit
reported three moderate affected entries, no high or critical entries, for the
`postcss-selector-parser` advisory
[GHSA-rj75-hqrm-r3gf](https://github.com/advisories/GHSA-rj75-hqrm-r3gf)
([CVE-2026-104844](https://nvd.nist.gov/vuln/detail/CVE-2026-104844)).
The locked parser was `6.1.4`; the advisory's fixed version is `7.1.6`.

`package.json` now overrides only that parser to exact version `7.1.6`.
The lock changes its single parser placement and integrity record. Tailwind,
PostCSS Nested, jsdom, Python dependencies and generated CSS are unchanged.
The earlier proposed PostCSS Nested major override was not adopted.

**Compatibility limitation:** both Tailwind and PostCSS Nested declare parser
`^6` ranges. This override crosses those ranges; passing the exercised paths does
not establish every upstream API's compatibility. Retain the exact pin and repeat
the checks below when any of these consumers or their configuration changes.
Do not apply a forced framework migration just because an audit suggests one.
The established exposure is in development/build tools; production request-path
reachability was not established.

## Executed local checks

- Node `22.23.3` explicitly invokes the existing npm `11.19.1` CLI. The first
  unavailable sibling-npm attempt is retained as a setup failure, not a clean
  audit. No global Node fallback or tool installation was used to resolve it.
- Fresh, credential-scrubbed outside-source `npm ci --include=dev` succeeds.
  Installed parser/Tailwind/PostCSS Nested/jsdom versions are
  `7.1.6` / `3.4.17` / `6.2.0` / `24.1.3`. Subsequent audits report zero findings.
- The real Tailwind CLI produces byte-identical committed CSS, SHA-256
  `2752136ac99210a29001190ae5ee1b23d1c12f4d79c348c705b38fd4fa2ff1b3`.
  No CSS rebaseline or assertion change was made.
- Worker Node reconciliation passes 27 cases; focused Python frontend wrappers
  and static-asset control pass three cases per supported interpreter. Parent
  integration independently repeats fresh staging, audit and CSS comparison, runs
  unchanged copied Node tests against the actual parent application and staged
  jsdom, and runs the parent Python frontend controls. The checkout's existing
  `node_modules` is preserved, not silently replaced or claimed freshly installed.

npm also warns about deprecated `whatwg-encoding` and an optional `fsevents`
install script not covered by its allow-scripts policy. No global script approval
was granted; a successful install is not evidence that every optional script ran.

## Python audit boundary

Read-only resolved runtime inventories on Python `3.11.17` and `3.12.15` each
contain 25 packages, with no missing active requirements. OSV queries for these
specific name/version pairs returned no matches. Test SDK, pytest and PyInstaller
were not misclassified as application runtime roots. No Python dependency change
was justified by that result. Broad minimum requirement ranges are not promises
that every historically allowed version is safe, and this was not a pip-audit run.
Refresh the resolved inventory/advisory check for the actual final artifact.

## Provenance and remaining gates

Reviewed two-file candidate: `255a8a88e65698c30d1aaa0626fb0f9236641bafe9e503543649c52373c9d02f`;
patch: `0690aed37706103539caef22792d51e50a3f781f8d7045d474d52e400e4e9d6c`.
Parent focused checks are bound to the 863-file source snapshot
`3c947d5c79f747c823bcdf427ae3a2d5b7572be1e07844a5152b9dc27d79b01b`,
before this status document was added. Do not transfer that whole-source identity
to subsequent documentation or runner changes.

Ignored `t27-release-dependency-audit-20261024/` retains raw baseline/candidate
audits, metadata inventories, review bindings, fresh-install/CSS/UI receipts and
parent integration checks. Misplaced worker candidate evidence was copied to its
intended parent evidence location with byte hashes; originals remain retained.

The preceding full suite still has three Python 3.12 failures, although their
unchanged targeted reruns and all 25 original/supplemental probes pass on both
versions. A new composed full-suite run, fresh wheels/native artifacts and the
remaining release gates are required. Neither this audit nor focused compatibility
closes G1, T27's final qualification, or release approval.

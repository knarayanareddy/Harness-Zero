# G1 — macOS checkpoint Git launcher prerequisite

Status: a narrow prerequisite is locally integrated and source-reviewed. G1 is
not declared green from this repair or from passing selected protocol groups.

The existing checkpoint path runs several Git processes before a first model
call and during stop hooks. Retained failures consume a two-second approval wait
before the approval stage, or consume completion time in stop-hook Git work.
Timed diagnostics show variable cold Git delays, not a lost approval event.
Host load alone does not establish their cause.

Paired controls use the same installed Apple Git2.50.1 through `/usr/bin/git` and
the executable reported by `xcrun --find git`. Small observed comparisons support
avoiding launcher overhead; they are not a general performance percentage or
proof of stable tail latency.

The repair resolves that actual executable only on Darwin when an unambiguous
absolute PATH selects exactly the system launcher. Custom, relative, empty,
symlink and non-Darwin routes keep the original behavior. Only validated successes
are cached, at most8, keyed by PATH and DEVELOPER_DIR. Discovery and Git spawn use
coherent routing environments; changes during lookup discard the result. Repeated
caller cancellation cannot abandon discovery-child cleanup before kill/reaping.
No environment contents are emitted into diagnostics.

Snapshot:
`9eb3a74500c2fe6dff8baebee09e69068ecc5c94892644d3cce582fd222b1e29`.
Complete original-base patch:
`ac794e471c136834e045c6bef452b20f2b9488c258386a60fbd8e20279bbf89d`.
Independent source/base review is clear.24 targeted worker cases and36 parent
resolver/checkpoint/workspace-change cases pass on each Python version. Git
arguments, cwd, private repository identity, excludes, size guards, snapshot and
restore behavior remain unchanged; no checkpoints or deadlines are disabled.

Earlier composed ACP timing groups remain red in their historical receipts.
After the owner confirms a quiet window, the unchanged77-case group passes on
both versions; the later real-parent414-case integration group passes per version.
This does not establish a universal response-time guarantee under resource
contention or replace full-suite, native/current-artifact and soak qualification.

Evidence is under ignored `.venv/evidence/g1-git-launcher-20261024/`, with timing
observations in `acp-checkpoint-latency-20261024/` and `acp-git-launcher-20261024/`.

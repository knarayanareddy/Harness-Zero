# T16 — chunk-independent XML fallback

Status: locally integrated; full/live/artifact qualification remains incomplete.

The parser retains partial closing-tag suffixes across feed calls, preserving
tool calls and following text. Malformed/incomplete tool blocks and non-object
arguments fail visibly, rather than silently dropping calls or coercing invalid
argument strings into executable input. Valid JSON-object-encoded strings remain
supported. Short ambiguous text suffixes such as `<` and `<t` remain plain text;
they are not evidence of a tool invocation.

Retained tool payloads are capped at 65,536 Unicode characters, with only the
bounded partial-tag suffix retained separately. This does not claim a bound on
the size of a single incoming provider chunk or all output/history buffers.
Parser ValueError propagates through the current adapter; it is not relabeled
as successful Done. No new parser authority bypasses the policy gate.

## Evidence (local ignored `.venv/evidence/`)

- `luna-t16/`: fail-first split closing tag, malformed corpus and oversized
  payload, then 10 selected checks pass on each Python version (7 deselected).
- Parent fail-first controls: `t16-parent-plaintext-red.xml` shows three benign
  trailing-text failures; `t16-parent-arguments-red.xml` shows malformed argument
  coercion. Both are repaired without relaxing malformed-call rejection.
- `t16-parent-py312.xml`: **24 passed in 0.95s**.
- `t16-parent-py311.xml`: **24 passed in 2.75s**.
- `t16-parent-r10.xml`: original R10 passes, 18 unrelated probes deselected.
- Final combined provider/media/XML/runner subset: **105 passed per Python
  version**, `t13-t16-final-py312.xml` and `t13-t16-final-py311.xml`.
- Luna verified the exact parent candidate
  `e2604ae087998fd5432f60d4c5dd8cdba48e42a989375cfa91e87e63def85968`
  and found no actionable issue. Source-only review, not independent test execution
  or independent patch hash verification. Parent checks both snapshot and patch
  hashes; `t16-acceptance-receipt.json` binds that acceptance.

Tests include every split of a representative ASCII tool/thinking/text stream,
one-character chunks, ordinary trailing text, malformed/oversized blocks, and
the actual adapter's native-tools HTTP400 → XML fallback failure path without
ToolCallEnd/Done. This is not an exhaustive Unicode-byte or whole-protocol audit.
No source assertion, golden, recording or existing deadline was weakened.

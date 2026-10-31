# Development workflow — effective 2026-10-22

This is the current **human/agent development process**, not a description of
already shipped Harness-Zero runtime automation. The owner requested the
[Zide-derived recommendations](../docs/research/ZIDE_WORKFLOW_REFERENCE.md) be
added to the workflow. Runtime acceptance remains unchecked in that reference
and the roadmap. Existing CONSTRAINTS.md and milestone gates still apply.

## 1. Establish a bounded task

Read `.claude/napkin.md`, `CONSTRAINTS.md`, `tasks/todo.md` and the owning roadmap
contract. State the task ID, source/base identity, writable files, exclusions,
acceptance commands and evidence location before editing. Preserve existing user
changes. Do not start new product features to make a repair look more complete.

## 2. Dispatch only when authorized

Use one integration owner. In the current user-approved Luna arrangement, at most
two implementation lanes and one independent read-only reviewer run concurrently.
Pass the exact catalog-resolved model per dispatch; do not change global defaults.
Other models/spend and broader delegation still require appropriate authorization.

Workers use distinct worktrees and disjoint write allowlists. Record whether
the worktree has the parent's uncommitted fixes; test imports must resolve to
the intended tree. Shared docs, ledgers, generators and integration belong to
the parent. Return cross-lane requirements rather than editing another lane.
Serialize full suites, builds, native qualification and stress work on this Mac;
small disjoint focused checks may run concurrently with their limits recorded.

## 3. Require an actual result and honest status

Dispatch acknowledgment is not model execution, changed code, passing tests or
completion. Inspect the actual diff and retain fail-first/green evidence.
Distinguish changed-unvalidated, test-failed, review-required, blocked, timeout,
cancelled, no-change, integrated and externally delivered results. Process output
or silence never proves whether human approval or a finished artifact exists.

## 4. Bind review and tests to the candidate

Before review, record task ID, base identity, allowed file paths, before/after
file hashes and a SHA-256 of the complete scoped candidate patch (including new
files, binary content/modes and renames where applicable). Keep synthetic/raw
evidence in ignored storage; never put credentials or user transcripts in it.

For an initial documentation/import task whose pre-edit dirty base was not
captured, say so and review a full scoped file snapshot with a reproducible digest.
Do not fabricate before-hashes or call that snapshot an apply-ready patch. Future
editing/integration tasks must capture their real scoped base before mutation.

### Reproducible regular-file snapshot identity

Full snapshots use `digest_format: "harness-zero.snapshot.v1"`. The `files` array
contains exactly `path`, `mode` and `sha256` string fields per entry:

- `path`: exact normalized POSIX spelling, relative to the explicitly selected
  checkout root, or an explicitly declared absolute path for approved external
  documents. Reject duplicates, backslashes, NUL and dot/parent path segments.
  Do not normalize Unicode filename spelling.
- `mode`: Python `oct(stat.S_IMODE(path.stat().st_mode))`, for example `0o644`.
- `sha256`: lowercase SHA-256 hex of the file's exact bytes, not normalized text.

Sort entries by their exact `path` strings in Unicode code-point order. Sort the
three object keys lexicographically, serialize compact JSON with non-ASCII
characters unescaped, encode UTF-8 and hash those bytes with SHA-256. No BOM,
indentation, trailing spaces or final newline belongs to the aggregate input:

```python
payload = json.dumps(sorted(files, key=lambda entry: entry["path"]),
                     ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
candidate_sha256 = hashlib.sha256(payload).hexdigest()
```

Fixed vector: entries `a.txt / 0o644 / "a" * 64` and
`z.txt / 0o755 / "b" * 64`, in either input order, produce
`9156490127927b98d588a02cb89571499a05e45ad2c92717e777aab2f5eb3137`.
The implementation and literal-byte vector are tested in
`tests/test_verify_review_snapshot.py`.

The read-only development verifier independently recomputes the aggregate and
checks every current regular file and mode against an expected digest taken
from the independent review receipt:

```sh
python scripts/verify_review_snapshot.py /path/to/manifest.json \
  --root /path/to/checkout --expected-digest REVIEWED_SHA256
```

Exit 0 means that scoped integrity check passed; exit 1 reports a mismatch or
invalid manifest as JSON. The verifier never repairs files or updates a digest.
Rehashing an edited file/manifest still fails against the earlier review digest.
Use trusted manifests: absolute external entries are explicit reads, not a new
permission grant. This is not an authenticated review service or a filesystem
sandbox; check reviewer provenance, task/base/repository identity and external
writer races separately. Top-level receipt metadata is not in this file-content
aggregate. Symlinks, symlinked path components and directories are rejected.
Deleted paths and rename provenance
need the complete base-bound patch, not this existing-regular-file snapshot alone.

The reviewer receipt names that digest, scope, model, findings and limits.
Verification records commands, interpreter/environment, result and artifact
locations against the same candidate. Source-only review remains source-only.
An unavailable reviewer does not silently become approval.

After any candidate change, refresh its identity and re-review/re-test affected
scope. A test run on a prior tree is historical evidence, not a pass for the new
candidate. Do not regenerate goldens or weaken deadlines merely to obtain green.

These are parent-enforced development requirements today. There is no new
machine-enforced review service or end-user Crosscheck feature implied here.

## 5. Recheck the integration destination

Compare the destination's branch/base and scoped before-content with the expected
snapshot immediately before applying the reviewed patch. Stop on divergence or
unexpected user edits. Reconcile deliberately and issue new receipts for the new
candidate; never reset, silently overwrite or auto-resolve conflicts.

This development check is **not** the runtime stale-Git-approval mechanism.
Implementing that mechanism requires ZIDE-04's separate policy design and tests;
do not claim a comparison eliminates all races with external filesystem writers.

## 6. Verify the combined public path

Run the owning negative/positive/interruption tests on the parent tree. Different
worker test results do not prove the combined runtime works. Check schema/docs
drift, lint and diff whitespace. Qualify actual installed/frozen artifacts when
required; a checkout-backed smoke is not distribution proof.

Keep required gates visibly open on failure/skip/unavailable native or live
evidence. Current G1 remains non-green. Complete existing repairs without promoting
a qualified build or widening authority on the strength of passing subsets.

## 7. Finish without inventing delivery

Update the evidence ledger and task status with exact scope and limitations.
Show a reviewable diff, actual verification and pending approval in one handoff.
Do not equate conversation rollback with undoing filesystem/Git effects. No
commit, push, release, remote interaction, paid live-provider call or OS security
change is authorized merely by this workflow or a favorable reviewer result.

## Runtime integration ownership

| Reference | Existing owners | Current status |
|---|---|---|
| ZIDE-01: task → worktree → child | T38, after existing prerequisites | Development isolation in use; product implementation pending |
| ZIDE-02: exact-change independent review | Current workflow; T37/T38 product handoff | Manual parent receipt binding required; runtime automation pending |
| ZIDE-03: attention/status/recovery | T23/T24 correctness, then T37 UX | Honest development statuses required; dashboard pending |
| ZIDE-04: stale repository approvals | Approval/policy slice extending T05; T17/T18 mutation admission | Destination recheck required now; runtime approval binding pending |

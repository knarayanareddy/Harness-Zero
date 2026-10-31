# Search Tools Plugin (`search-tools`)

Provides regex codebase search (`search_codebase`):
- Uses one descriptor-confined Python reader on supported Linux/macOS hosts; no executor or `exec` capability is needed.
- Opens each file/directory relative to its acquired parent descriptor with `O_NOFOLLOW`. A swapped leaf or parent pathname cannot redirect the open outside the acquired workspace tree.
- Discovered file/directory symlinks are skipped, even if their target is inside the workspace. An explicitly requested directory alias may be canonicalized to an inside target by the gate; outside targets are denied. The reader itself does not follow links.
- Searches only regular files, opens candidates non-blocking so a FIFO cannot stall traversal, and prunes ignored directories before descending.
- Supports case sensitivity, glob filtering, result capping, and offset-based pagination.
- Fails closed if descriptor-relative no-follow operations are unsupported or the requested root cannot be opened safely. Inaccessible/disappeared entries are skipped; readable siblings remain searchable.
- The old ripgrep path was unreachable through the real read-only tool's capability intersection and could reopen mutable paths in alternate contexts; it is removed rather than left as an unsafe bypass.

Scope: descriptor anchoring authorizes the acquired workspace directory objects;
it is not a native process sandbox or protection against arbitrary code running
as the same OS user, hard links, or unrelated filesystem tools. Search order is
filesystem order, not a new ordering guarantee. Regex CPU budgets and broader
large-repository performance are separate concerns; no performance gain is claimed.

Regression evidence: `tests/kernel/test_search_confinement.py` crosses the real
locked-mode gate and injects leaf, parent and root swaps at open time without
sleeps. The original `review/regressions/test_review.py::test_R01_locked_search_cannot_read_outside_symlink`
remains the review-level closure probe. No other review finding is fixed here.

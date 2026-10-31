# G1 prerequisite — headless CLI imports

Status: locally tested and independently source-reviewed. G1 remains non-green.

MCP/ACP command startup no longer initializes the unused interactive CLI frontend
or Markdown renderer. Console-specific imports occur inside the one-shot and
interactive execution functions, before their exception handlers. Command, policy,
credential, request/result and cleanup semantics are unchanged.

An instrumented Python3.11 startup observation took2.0847s to its first response;
rich.markdown accounted for346ms cumulative import time. Import profiling and a
one-second stack dump perturb timing. This is evidence of avoidable work, not a
performance percentage, universal startup SLA or sole explanation of prior failures.

Evidence under `.venv/evidence/g1-headless-cli-imports-20261023/`:
- Fresh-process import blockers reject console rendering during bare CLI import
  and actual MCP initialization. Both cases fail first on the original source.
- An actual offline mock-provider one-shot run still loads and exercises console
  rendering. The child environment excludes credentials and inherited
  HARNESS_MOCK_SCRIPT; a regression verifies the parent environment is unchanged.
-19 CLI/import checks pass per Python version. A separate64-case MCP audit/CLI/
  read-ledger/settings/secrets group passes per version on identical production
  CLI bytes. These are focused groups, not a full-suite result.
- Initial home-directory fixture errors and a new fixture's incorrect server-name
  expectation are retained and corrected to the actual unchanged contract; neither
  is misreported as a production behavioral failure.
- Final snapshot `da3e6a7a0f64913be1904e194c725a1d409311a258dcac4efc6cba18ebc3a2ef`;
  actual base-bound patch `be05d1c02c5790b447f46127dcab489eea58884363d2c8b01d0611b894de6f40`.
  Independent source/integrity review is clear; parent rechecks both before local
  acceptance. Reviewer does not execute tests. See `acceptance-receipt.json`.

The accepted import moves are propagated through an explicit input amendment to
the held T19 tree, preserving its exact ACP/MCP handler source and old manifests.
One unchanged32-case strict matrix then passes on each Python version in that
isolated tree. The prior3.11 timeout is retained. T19 exact final-candidate review,
parent CLI composition/integration and fresh parent tests remain separate; the
parent ACP entrypoint is not reported repaired by an isolated-tree pass.

The separate headless-question1s gate, full-suite, official ACP interoperability,
native/live/final-artifact qualification and required soak remain open.

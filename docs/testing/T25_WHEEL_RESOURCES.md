# T25 — installed wheel resources

Status: resource repair locally integrated and independently source-reviewed.
This is not a release-qualified distribution or frozen/native-app qualification.

Hatch force-includes the original builtin/skills tree under harness/builtin/skills
inside the wheel. The shared reader selects that package-relative root, retaining
the explicit checkout fallback and all R20 descriptor/no-follow behavior. No
workspace authority, CWD fallback, skill bytes or runtime dependency is added.

## Actual artifact checks

The default packaging test builds a direct wheel, an sdist and a wheel rebuilt
from that sdist. It verifies exact original bytes for all four builtin skills,
all 44 plugin manifests and 83 checked entrypoint resources. Both wheels are
installed non-editably into separate disposable targets outside the checkout.
The probes remove PYTHONPATH, run with -I -S, and assert every loaded Harness
module comes from the artifact target. They discover/view all four skills,
reject R20 outside-file canaries, resolve registered UI modules, and execute an
offline mock CLI turn through the installed kernel. This is not browser testing.

Parent noticed that CI installs .[dev], but the new default test needs build and
the Hatchling backend outside build isolation. Both tools are now declared in
the dev extra, not runtime dependencies. A normalized PEP 508 contract test fails
first before that declaration (`t25-parent-dev-tools-red.xml`).

## Evidence (`.venv/evidence/`, local/ignored)

- Worker initial built wheel contains zero builtin skills. Its local resource
  fix passes inventory/install checks; independent source review verifies exact
  candidate and patch with no findings.
- Parent checks all three destination hashes against the worker seed before
  integrating. Parent adds only the dev-tool declaration and its fail-first test.
- Parent Python 3.12: **two packaging/dev-contract tests pass**, exercising direct
  and sdist-rebuilt wheels outside the checkout (`t25-parent-py312.xml`).
- The existing Python 3.11 environment lacks the backend and three pure-Python
  dependencies. Parent uses an owned offline overlay of the already-installed,
  version/Python-compatibility/byte-verified build tools, without modifying shared
  environments or downloading anything. The artifact probes remove that overlay
  from their import path. **Two tests pass**, including actual Python 3.11 builds
  and both installed artifact probes (`t25-parent-py311-overlay.xml`). This is
  not presented as a fresh pip-installed Python 3.11 dev environment.
- R20/source/ledger: **29 passed on Python 3.12**, **32 on Python 3.11** including
  supplemental probes; original R19 plus supplemental skills: **four passed on
  Python 3.12**. Later all **16 repaired original probes pass together** on 3.12.
- Earlier default pytest temporary artifacts expired before receipt collection.
  They are not claimed retained. Serial reruns use dedicated owned basetemps:
  `t25-parent-retained-py312.xml` and `...-py311.xml`, two passed per version.
  `t25-parent-retained-artifacts.json` retains and hashes **six actual artifacts**.
- Before/after build inputs are identical, digest
  `25cdb4823e4a36fe763b38b282f5e380ba063da7bdbb3a17a6a6c4190a22e869`.
  Retained files precede the later MCP output-backpressure integration. They are
  resource-test artifacts, not the latest complete product distribution; rebuild
  and requalify final app bytes before any distribution.
- Independent Luna review verifies parent snapshot
  `2df095cd6675c73ac977ce49cef100660ef673c6af9ea8237d31c289bfdeaadb`
  and actual complete patch SHA
  `ae2a338182f094d2286f874ab5b0ee5f4a9f9bdb08e11a17e93c285bde9e4f05`
  with no findings. Reviewer reads source/evidence, not rerunning builds/tests.
  Parent rechecks scoped integrity and all six artifact hashes before accepting;
  see `t25-acceptance-receipt.json`.

Official build contract checked: https://hatch.pypa.io/latest/config/build/#forced-inclusion

Linux execution, a fresh minimal-runtime-dependency environment, frozen Studio,
signing/notarization, live-provider behavior, full suite and soak remain separate
qualification lanes. No artifact has been published or delivered as a release.

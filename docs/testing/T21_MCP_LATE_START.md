# T21 — register tools after initial MCP recovery

Status: locally integrated; G1/global qualification remains incomplete.

The initial successful handshake still seeds the client cache, allowing plugin
setup to register tools once. A later successful handshake now applies the tool
catalogue through the existing callback even if the initial connection never
populated `_tools`. Previously the client could report connected while no tool
became callable in the kernel. The production change is one condition; no new
registry, callback API, provider or privilege is introduced.

## Evidence (`.venv/evidence/`, local/ignored)

- `luna-t21/`: worker observes fail-first connected state with missing registry
  tool, then a real late stdio peer/gate test passes on each Python version;
  four selected reconnect/cancel/HTTP catalogue controls also pass per version.
- Parent `t21-parent-red-diagnostic.xml`: connected was observed, but the
  post-registration catalogue event never arrived. An earlier red attempt hit
  its outer reporting limit with an empty log; it is not counted as completed.
- Parent replaces test polling with the callback's post-registration event and
  holds the synthetic peer's reconnect readiness using a fixture marker. The
  disconnected-call assertion cannot silently race instant reconnection.
- `t21-parent-py312.xml`: **14 passed in 10.16s**.
- `t21-parent-py311.xml`: **14 passed in 10.73s**.
- Original R11 passes (`t21-parent-r11.xml`).
- Later combined CLI/server/client subset: **35 passed on Python 3.12** in
  `t18a-t21-parent-py312.xml`; three original MCP probes pass in
  `t18a-t21-original.xml`. This is not a full-suite result.
- Luna source review verifies exact parent snapshot
  `c4a381e1e354bb59e203acad29e4938139b33b5e70e5cdfa24a064a13192df2c`
  and finds no actionable issue. Parent checks the complete patch hash; reviewer
  does not execute tests or independently hash the patch. See the acceptance receipt.

The actual runtime registry/gate invokes the late tool, preserves one registration
on unchanged recovery, replaces a changed definition, removes a deleted tool and
returns errors while disconnected. Registration counts are observed on the real
registry, not a replacement executor. There are no test correctness sleeps.

One broader worker Python 3.11 HTTP attempt failed during initial handshake;
diagnostic/isolated reruns and both parent subsets pass. Its root cause is not
established or claimed fixed. That caveat remains in worker evidence. No live
service, required timeout, assertion, golden, full matrix or artifact claim changes.

# G1 prerequisite — unused provider HTTP clients

Status: lifecycle repair locally accepted; G1/T01 remains non-green.

Anthropic, Gemini and Ollama now create owned HTTP clients only on actual use,
not constructor/bootstrap or unused teardown. Injected clients remain borrowed,
including falsey instances. Owned close prevents late client creation. Existing
timeouts, URLs, authentication/header logic, streaming and TLS verification are
unchanged. OpenAI-compatible was already lazy and is outside this change.

Actual host-pressure stacks showed Gemini SSL-context construction during
bootstrap. Eliminating unused transports targets that work; it does not establish
a sole cause or a measured percentage improvement.

Local evidence under `.venv/evidence/`:
- The original red run contains nine genuine lifecycle failures and one home-folder
  fixture failure. All are retained; no false claim of ten behavioral red cases.
- The new wire fixtures were corrected to the actual Ollama OpenAI-compatible SSE
  endpoint and preserved native terminal reasons; production behavior was not
  changed to satisfy a wrong mock. Bootstrap is a post-fix registration control.
- Ten lifecycle/wiring checks and95 provider/media/streaming regressions pass per
  Python version. Strict unchanged headless1s passes3.12 but fails3.11 at about1.918s.
- Independent source/integrity review verifies snapshot
  `374f720a79024348ef73eb549769cb3eaaf102a18fff28131b2829310f08d52f`
  and actual patch `17be26f630a92bc73a330265887f5f6b4e88cd7cdf588c8611ad1ae188f202ee`
  with no findings; reviewer does not execute tests. Parent rechecks both before
  accepting; see `g1-provider-lazy-acceptance-receipt.json`.

The held ACP tree receives these exact three provider files through a separate
parent-owned input amendment; original seed manifests remain unchanged. No global
dependency, process, OS or security setting is altered. Full-suite, live-provider,
native, final artifact and strict timing qualification remain separate and open.

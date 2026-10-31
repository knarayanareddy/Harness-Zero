# Local Executor Plugin (`local-executor`)

Default process executor. The gate validates paths/permissions; native sandbox
wrappers, when available and required, confine the payload. The supervisor is
trusted control-plane code, not itself a filesystem sandbox.

## Features
- **Owned group identity**: an isolated stdlib supervisor anchors a new session/group, self-signals only that live group through private control pipes, and stops it on owner EOF. No numeric-PGID fallback after reuse.
- **Environment Scrubbing**: Whitelists safe environment variables (`PATH`, `HOME`, `LANG`, etc.) preventing credential leakage from parent environment.
- **Output Streaming**: Streams stdout and stderr chunks in real time without blocking.
- **Graceful Termination**: Sends `SIGTERM` first, allowing graceful shutdown before escalating to `SIGKILL`.
- **Transparent payloads**: stdin/stdout/stderr, literal argv and signed exit status are preserved. Private status/control/wakeup FDs and bootloader state never reach payloads. Interpreter/bootloader controls are reserved, not tool environment settings.
- **Completion ownership**: foreground payload completion also closes inherited descendants. Use Jobs/background handoff for independent work; untracked `&` children do not outlive their foreground scope. `pid` identifies the supervisor.
- **Installed/frozen entry**: uses the running interpreter and absolute SDK helper, or an early frozen-engine self-entrypoint with trusted worker state; no ambient Python helper lookup. Unexpected supervisor loss is unconfirmed, not a successful stop.

Deliberate session/group escape and arbitrary same-user code remain outside this
guarantee. Native sandbox availability and Linux/frozen qualification are reported
separately; see `docs/testing/T06_SUPERVISED_CANCELLATION.md` for evidence/limits.

# WS-09 — MCP client: streamable-HTTP transport, per-call timeout, cancellation, reconnect

| | |
|---|---|
| Status | implemented and verified on the current branch; no commit or push |
| Depends on | WS-01 (scenario with the echo fixture); WS-02 (settings declared) |
| Effort | 1–2 days |
| Principles | P3, P6 |
| dsh reference (@86a40220) | `packages/features/mcp-client/` (transports `stdio` + `streamable-http`, header auth, per-call `timeoutMs`, `notifications/cancelled` on abort, reconnect with backoff and tool re-listing, `mcp.server.status` events), `packages/features/mcp-client/README.md` |
| Harness-Zero touchpoints | `client.py` now dispatches through `transports/{stdio,http}.py`, owns request correlation/timeouts/cancellation, refreshes tool lists and exposes reconnect status; `supervisor.py` provides bounded jittered backoff; `plugin.py` supports stdio/HTTP configuration, settings aliases, secret-backed headers, status and changed-tool events; generated tools remain `mcp__<server>__<tool>` with the Addendum A.2 annotation-to-risk behavior; `harness/cli.py` preserves `harness mcp` as the stdio server and adds `harness mcp status`; tests exercise kernel HTTP JSON/SSE, cancellation/timeout, reconnect and CLI diagnostics. |

## 1. Outcome

- Server config accepts `{"transport": "stdio", "command", "args", "env"}` **or** `{"transport": "http", "url", "headers", "auth": {"bearer_env": "MY_TOKEN"}}`. HTTP follows the MCP *Streamable HTTP* transport (single endpoint, `POST` JSON-RPC, optional SSE response stream, `Mcp-Session-Id` header, `GET` for server-initiated stream).
- Every `call_tool` has a timeout (`plugins.bridge-mcp-client.call_timeout_s`, default 60, overridable per server) and, when the kernel cancels the tool (WS-03 cooperative timeout / user stop), the client sends `notifications/cancelled {requestId}` and discards late results.
- Disconnects are detected (stdio EOF / HTTP 5xx, connection errors); the client reconnects with exponential backoff (1, 2, 4 … 60 s, jitter), re-runs `initialize` + `tools/list`, and **re-registers tools only if the list changed** (emits `mcp.tools.changed`). While disconnected, `mcp__<server>__*` calls return an error result immediately (`MCP server 'x' is reconnecting (attempt 3); try again shortly`) — no hanging turn (P3).
- `harness mcp status` shows per-server transport, state (`connected | reconnecting | failed`), tool count, reconnect attempt, last error and whether a `Mcp-Session-Id` is present; it does not expose auth headers or session identifiers. `harness mcp` without a subcommand remains the MCP server entry point.
- Secrets: `headers` values may reference `${secrets.NAME}` resolved through the kernel `Secrets` store — never literal tokens in settings.

## 2. Implemented state (verified)

- Stdio and Streamable HTTP are implemented through a transport seam. HTTP handles JSON responses, SSE responses, `Mcp-Session-Id`, and the optional GET notification stream.
- Calls have a configurable timeout and cooperative cancellation; cancellation/timeout sends `notifications/cancelled` best-effort, clears the pending request and ignores late results.
- Disconnects enter bounded exponential backoff with jitter, then reinitialize and re-list tools. Only additions, removals and changed definitions are re-registered; `mcp.tools.changed` is emitted and durably recorded.
- `mcp.server.status` reports connecting/connected/reconnecting/failed/stopped state. HTTP headers resolve `${secrets.NAME}` references or an environment-backed bearer token; diagnostics omit credentials and session IDs.
- Tool names and annotation-derived risk classes remain unchanged. The expanded fixture/tests cover JSON and SSE through the kernel, notification-driven list refresh, reconnect recovery, cancellation, per-call timeout, secret/env auth and `harness mcp status`.

## 3. Design

### 3.1 Transport seam inside the plugin

```python
class Transport(Protocol):
    async def start(self) -> None
    async def send(self, msg: dict) -> None
    def incoming(self) -> AsyncIterator[dict]          # yields JSON-RPC messages
    async def close(self) -> None
    @property
    def alive(self) -> bool

class StdioTransport(Transport): ...                    # today's code moved here
class StreamableHttpTransport(Transport):
    def __init__(self, url: str, headers: dict[str, str], *, http: httpx.AsyncClient): ...
    # POST each request; if response is `text/event-stream`, consume events until the matching id resolves;
    # keep Mcp-Session-Id from initialize; GET stream task for notifications; 404 on session ⇒ re-initialize
```

`MCPClient` keeps its public API; it owns a `Transport`, a `pending: dict[id, Future]`, and a **supervisor task** implementing the state machine `connecting → connected → reconnecting(n) → failed(after max_attempts)`.

### 3.2 Cancellation

`call_tool(name, arguments, *, cancel_event)`: wait on the future **or** `cancel_event`; on cancel → `notify("notifications/cancelled", {"requestId": id, "reason": "cancelled by harness"})`, remove pending, raise `asyncio.CancelledError` → kernel turns it into the cooperative-timeout result (WS-03).

### 3.3 Settings (`settings.schema.json`, namespace `plugins.bridge-mcp-client.`)

| key | default |
|---|---|
| `servers` | `{}` (object of name → server config; schema with `oneOf` stdio/http) |
| `call_timeout_s` | 60 |
| `connect_timeout_s` | 15 |
| `reconnect.max_attempts` | 10 |
| `reconnect.max_backoff_s` | 60 |
| `register_tools_as` | `mcp__{server}__{tool}` (documented, not configurable in v1 — listed read-only) |

Legacy keys (`plugins.mcp-client.servers`, `extensions.mcp_servers`) read with a `system.notice` deprecation for one minor version, then removed.

### 3.4 Events

`mcp.server.status {server, transport, state, attempt, error, tools_count, session_id_present}` (live), `mcp.tools.changed {server, added, removed}` (durable → record `system.notice` with `model_visible: true` so the model learns tools appeared/disappeared).

## 4. Files

### ADD

```
harness/plugins/bridges/mcp_client/transports/{__init__.py,stdio.py,http.py}
harness/plugins/bridges/mcp_client/supervisor.py
tests/fixtures/mcp_echo_http_server.py             # aiohttp app speaking streamable-http (JSON + SSE responses), with optional flaky mode
tests/kernel/test_mcp_http_through_kernel.py       # kernel roundtrip, tool-list updates, auth
tests/kernel/test_mcp_reconnect.py                # reconnect and fast-fail behavior
tests/kernel/test_mcp_cancel.py                   # cancellation and timeout
tests/plugins/bridges/test_mcp_client.py          # stdio plugin, tool names/risk, reconnect status
tests/frontends/test_cli_frontend.py              # `harness mcp status`
```

### MODIFY

```
harness/plugins/bridges/mcp_client/{client.py,plugin.py,settings.schema.json,README.md}
harness/kernel/{registry.py,bootstrap.py,context.py,settings.py,catalog.py}  # plugin lifecycle, durable events, aliases, declaration checks
harness/sdk/events.py                              # MCP status and tool-change payloads
harness/frontends/web/static/events.js             # typed SSE event allow-list
harness/cli.py                                     # preserve server mode; add `harness mcp status`
docs/ARCHITECTURE.md
docs/catalog/*
docs/events.schema.json
review/dsh-integration/{WS-09_mcp_http_transport.md,plan.yaml,README.md}
review/REMEDIATION_PLAN.md                          # S14 now verifies recovery rather than terminal failure
```

## 5. Verification

| test | verified behavior |
|---|---|
| `tests/kernel/test_mcp_http_through_kernel.py::test_http_echo_tool_roundtrip` | kernel/gate roundtrip for JSON and SSE responses, session headers, MCP tool naming and risk |
| `test_http_tool_list_changes_re_register_only_deltas` | GET-stream notification refresh, changed-only registration/removal, durable event and model-visible notice |
| `test_http_auth_resolves_secrets_and_environment_without_leaking_status` | secret-store header interpolation, environment bearer auth, diagnostics do not contain credentials |
| `tests/kernel/test_mcp_reconnect.py` and S14 in `tests/plugins/bridges/test_mcp_client.py` | stdio restart recovery, HTTP session recovery after a 503, fast failure while disconnected, status transitions and successful post-reconnect calls |
| `tests/kernel/test_mcp_cancel.py` | cooperative cancellation and bounded per-call timeout, cancelled notification, late-result discard |
| `tests/frontends/test_cli_frontend.py::test_cli_mcp_status_reports_client_transport_state` | CLI transport/state/tool-count diagnostics |
| `tests/kernel/test_settings_declared.py` and catalog tests | MCP settings/aliases are declared and generated reference docs stay current |

Verified on the current worktree (not committed or pushed):

- Focused MCP/plugin/CLI group: **28 passed**.
- Flow-of-information scenarios: **54 passed, 1 skipped** (after `npm ci`; skip is the platform-dependent sandbox scenario).
- Non-scenario suite: **664 passed, 3 skipped**; the scenario marker was deselected.
- `ruff check` on touched Python files and `git diff --check`: **passed**.
- `harness catalog --check`: drift, all settings reads (73 declared), UI and references pass. Advisory warnings remain for 36/43 incomplete plugin READMEs (the MCP client README is complete) and 10 scenario-coverage gaps, including the shared `read_resource` tool; HTTP-specific behavior is covered by the dedicated kernel tests.

Reproduce with:

```bash
npm ci
.venv/bin/pytest tests/plugins/bridges/test_mcp_client.py tests/kernel/test_mcp_http_through_kernel.py tests/kernel/test_mcp_reconnect.py tests/kernel/test_mcp_cancel.py tests/frontends/test_cli_frontend.py -q
.venv/bin/pytest tests/scenarios -q
.venv/bin/pytest tests -m 'not scenario' -q
.venv/bin/ruff check harness/cli.py harness/kernel/catalog.py harness/plugins/bridges/mcp_client tests/fixtures/mcp_echo_http_server.py tests/kernel/test_mcp_http_through_kernel.py tests/kernel/test_mcp_reconnect.py tests/kernel/test_mcp_cancel.py tests/kernel/test_durable_events.py tests/plugins/bridges/test_mcp_client.py tests/frontends/test_cli_frontend.py
git diff --check
```

To inspect a configured endpoint, `harness mcp status -w /tmp/ws` shows its current state; `harness mcp -w /tmp/ws` remains the stdio MCP-server mode. The in-process HTTP fixture is used by pytest; the standalone fixture also supports `--flaky` for manual reconnect checks.

## 6. Non-goals / cautions

- No OAuth flow; bearer/static headers via secrets only.
- No MCP *server* changes (`harness/frontends/mcp` if present) — client only.
- SSE-only legacy transport (pre-2025 "HTTP+SSE") is not implemented; document it.
- Do not register MCP tools with `RiskClass.READ` by default; keep the A.2 rule (READ only when `readOnlyHint` is true).

## 7. Original agent brief (completed)

> Implement WS-09 from `review/dsh-integration/WS-09_mcp_http_transport.md`. Extract a `Transport` seam, move stdio into `transports/stdio.py`, add `transports/http.py` (Streamable HTTP with `Mcp-Session-Id`, JSON or SSE responses, GET notification stream), a supervisor state machine with backoff and tool re-registration, cancellation via `notifications/cancelled`, status events, and the settings schema. Write `tests/fixtures/mcp_echo_http_server.py` first (with `--flaky`) and make `test_http_echo_tool_roundtrip` red. Keep tool naming and the A.2 behaviours unchanged (existing `test_mcp_tool_through_kernel.py` must pass). Open PR `WS-09: MCP streamable-http + resilience`.

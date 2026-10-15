# WS-09 — MCP client: streamable-HTTP transport, per-call timeout, cancellation, reconnect

| | |
|---|---|
| Status | specified |
| Depends on | WS-01 (scenario with the echo fixture); WS-02 (settings declared) |
| Effort | 1–2 days |
| Principles | P3, P6 |
| dsh reference (@86a40220) | `packages/features/mcp-client/` (transports `stdio` + `streamable-http`, header auth, per-call `timeoutMs`, `notifications/cancelled` on abort, reconnect with backoff and tool re-listing, `mcp.server.status` events), `packages/features/mcp-client/README.md` |
| Harness-Zero touchpoints | `harness/plugins/bridges/mcp_client/client.py:58-280` (hand-rolled JSON-RPC over **stdio only**: `start`, `_read_loop`, `request(method, params, timeout=60)`, `list_tools/call_tool/list_resources/read_resource/list_prompts/get_prompt`, `stop`, `get_status`), `plugin.py:34-70` (reads `servers` from `plugins.bridge-mcp-client.servers` with two legacy fallbacks; each server `{command, args, env}`), tool naming `mcp__<server>__<tool>`; `tests/fixtures/mcp_echo_server.py` + `harness_mcp_server_runner.py` (stdio echo server); review Addendum A.2 (handlers fixed to take dict args; `*Hint` annotations) ; `harness/cli.py:521` (`harness mcp` status command) |

## 1. Outcome

- Server config accepts `{"transport": "stdio", "command", "args", "env"}` **or** `{"transport": "http", "url", "headers", "auth": {"bearer_env": "MY_TOKEN"}}`. HTTP follows the MCP *Streamable HTTP* transport (single endpoint, `POST` JSON-RPC, optional SSE response stream, `Mcp-Session-Id` header, `GET` for server-initiated stream).
- Every `call_tool` has a timeout (`plugins.bridge-mcp-client.call_timeout_s`, default 60, overridable per server) and, when the kernel cancels the tool (WS-03 cooperative timeout / user stop), the client sends `notifications/cancelled {requestId}` and discards late results.
- Disconnects are detected (stdio EOF / HTTP 5xx, connection errors); the client reconnects with exponential backoff (1, 2, 4 … 60 s, jitter), re-runs `initialize` + `tools/list`, and **re-registers tools only if the list changed** (emits `mcp.tools.changed`). While disconnected, `mcp__<server>__*` calls return an error result immediately (`MCP server 'x' is reconnecting (attempt 3); try again shortly`) — no hanging turn (P3).
- `harness mcp` and `status` show per-server: transport, state (`connected | reconnecting | failed`), tools count, last error, `Mcp-Session-Id` presence.
- Secrets: `headers` values may reference `${secrets.NAME}` resolved through the kernel `Secrets` store — never literal tokens in settings.

## 2. Current state (verified)

- stdio only; `request()` has a 60 s default timeout but `call_tool` is never cancelled and a dead server leaves tools registered that fail with a read-loop error. No reconnect. No status events. Settings undeclared (WS-02 fixes the schema; this WS adds the keys).

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

`mcp.server.status {server, transport, state, attempt, error}` (live), `mcp.tools.changed {server, added, removed}` (durable → record `system.notice` with `model_visible: true` so the model learns tools appeared/disappeared).

## 4. Files

### ADD

```
harness/plugins/bridges/mcp_client/transports/{__init__.py,stdio.py,http.py}
harness/plugins/bridges/mcp_client/supervisor.py
harness/plugins/bridges/mcp_client/settings.schema.json
tests/fixtures/mcp_echo_http_server.py             # aiohttp app speaking streamable-http (JSON + SSE responses), supports a `--flaky` flag that drops the connection every N calls
tests/kernel/test_mcp_http_through_kernel.py
tests/kernel/test_mcp_reconnect.py
tests/kernel/test_mcp_cancel.py
tests/scenarios/{mcp_http_echo,mcp_server_dies_and_returns}/…
```

### MODIFY

```
harness/plugins/bridges/mcp_client/client.py       # transport-agnostic; cancel; status machine
harness/plugins/bridges/mcp_client/plugin.py       # config parsing (oneOf), secrets resolution, re-registration on tools change, status events
harness/plugins/bridges/mcp_client/README.md       # transports, config examples, limitations
harness/cli.py:521                                 # `harness mcp` shows transport/state/attempt
pyproject.toml                                     # httpx already a dependency (used by tests) — move to runtime deps if not already
```

## 5. Tests (fail-first)

| test | fails on main because |
|---|---|
| `test_mcp_http_through_kernel.py::test_http_echo_tool_roundtrip` | no http transport |
| `test_mcp_reconnect.py::test_server_restart_reconnects_and_tools_still_work` (kill stdio child, restart) | tools stay dead |
| `test_mcp_reconnect.py::test_calls_while_disconnected_fail_fast` | hang until 60 s |
| `test_mcp_cancel.py::test_cancel_sends_notification_and_discards_late_result` | — |
| `tests/kernel/test_settings_declared.py` (WS-02) for this plugin | keys undeclared |

## 6. Acceptance

```bash
pytest tests/kernel/test_mcp_*.py -q && pytest tests/scenarios -q -k mcp
python tests/fixtures/mcp_echo_http_server.py --port 8931 &   # then configure {"echo_http": {"transport":"http","url":"http://127.0.0.1:8931/mcp"}}
harness mcp -w /tmp/ws      # echo_http: http connected tools=2
harness run … "call echo" → works; kill server → next call fails fast with the reconnecting message; restart → works
harness catalog --check
```

## 7. Non-goals / cautions

- No OAuth flow; bearer/static headers via secrets only.
- No MCP *server* changes (`harness/frontends/mcp` if present) — client only.
- SSE-only legacy transport (pre-2025 "HTTP+SSE") is not implemented; document it.
- Do not register MCP tools with `RiskClass.READ` by default; keep the A.2 rule (READ only when `readOnlyHint` is true).

## 8. Agent brief

> Implement WS-09 from `review/dsh-integration/WS-09_mcp_http_transport.md`. Extract a `Transport` seam, move stdio into `transports/stdio.py`, add `transports/http.py` (Streamable HTTP with `Mcp-Session-Id`, JSON or SSE responses, GET notification stream), a supervisor state machine with backoff and tool re-registration, cancellation via `notifications/cancelled`, status events, and the settings schema. Write `tests/fixtures/mcp_echo_http_server.py` first (with `--flaky`) and make `test_http_echo_tool_roundtrip` red. Keep tool naming and the A.2 behaviours unchanged (existing `test_mcp_tool_through_kernel.py` must pass). Open PR `WS-09: MCP streamable-http + resilience`.

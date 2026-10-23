# bridge-mcp-client

## Summary

Connects external Model Context Protocol servers over stdio or Streamable HTTP, discovers their tools,
and registers each one as a native Harness tool named `mcp__<server>__<tool>`. Calls cross the normal
gate, so a server tool is subject to the same permission mode, approvals, jail and redaction as a
built-in one. A failed initial connection is isolated to that server; configured reconnect attempts
recover transient failures without aborting the kernel boot.

## Configuration

<!-- GENERATED table — `harness catalog readme bridge-mcp-client` -->
| Key | Default | Meaning |
|---|---|---|
| `plugins.bridge-mcp-client.servers` | `{}` | External MCP servers, keyed by server name. Tools appear as `mcp__<server>__<tool>`. The former `plugins.mcp-client.servers` and `extensions.mcp_servers` spellings are temporarily accepted with a deprecation notice. |
| `plugins.bridge-mcp-client.call_timeout_s` | `60` | Default per-call timeout in seconds; a server entry may override it. |
| `plugins.bridge-mcp-client.connect_timeout_s` | `15` | Connection and discovery request timeout in seconds. |
| `plugins.bridge-mcp-client.reconnect` | `{"max_attempts":10,"max_backoff_s":60}` | Bounded exponential reconnect policy. |

stdio example:

```json
{"plugins": {"bridge-mcp-client": {"servers": {
  "local": {"command": "uvx", "args": ["mcp-server-example"], "env": {"EXAMPLE_CONFIG": "..."}}
}}}}
```

Streamable HTTP example with secret-backed headers:

```json
{"plugins": {"bridge-mcp-client": {"servers": {
  "hosted": {
    "transport": "http",
    "url": "https://mcp.example.test/mcp",
    "headers": {"X-Api-Key": "${secrets.MCP_API_KEY}"},
    "call_timeout_s": 30
  }
}}}}
```

For a bearer token supplied by the process environment, use `"auth": {"bearer_env": "MCP_BEARER_TOKEN"}`.
The referenced variable must be present when the plugin starts. Header values and session IDs are not
included in MCP status diagnostics. Configure secrets with Harness' secret store rather than writing
credentials into ordinary settings. The settings panel edits the same `plugins.bridge-mcp-client`
namespace.

## Extension points

- Registers one Harness tool for each discovered server tool, plus `read_resource` for MCP resources.
- Handles tool-list changes by unregistering removed or modified definitions and registering only the
  additions or updated definitions. A durable `mcp.tools.changed` notice describes each actual change.
- Emits `mcp.server.status` snapshots for connection, reconnect and terminal states. `harness mcp status`
  displays each configured server's transport, connection state, tool count and reconnect attempt.
- Uses a transport-independent JSON-RPC client with stdio and Streamable HTTP transports. HTTP supports
  JSON responses, SSE responses, `Mcp-Session-Id` session headers and the optional GET notification stream.

## Model experience

Tool names remain `mcp__<server>__<tool>` and schemas come from the server's `tools/list`. Text content
blocks and supported base64 images retain their order; image-only tool results remain images.
The handler validates MIME/base64 fields and caps aggregate encoded image data at 48 KiB before
copying/decoding it. This is a post-parse bound, not a transport memory ceiling. Empty or unsupported
content fails explicitly. Anthropic serialization is locally verified; currently unsupported tool-image
modes in the other adapters fail explicitly rather than silently dropping images. No live vendor
compatibility or image-content/OCR redaction is implied. See
[T22 evidence](../../../../docs/testing/T22_MCP_MEDIA.md). Risk class is derived
from the server's `ToolAnnotations`: `readOnlyHint` → `read`, `destructiveHint` → `destructive`,
`openWorldHint` → `network`, and an unannotated tool → `write` (which means approval is required in
`ask` mode — an unlabelled tool is not trusted, it is treated as a mutation).

Tool calls use the configured per-call timeout. On kernel cancellation or timeout the client sends
`notifications/cancelled` best-effort, drops the pending response, and ignores late replies. A lost
connection enters bounded backoff, re-runs initialization and `tools/list`, and restores changed tool
definitions. Calls made while disconnected fail promptly with the current connection state.

## Known limitations

- stdio server environment inherits only the scrubbed Harness environment plus explicitly configured
  server variables. Use the separate secret store for HTTP auth references; do not put credentials in
  public tool descriptions or status data.
- Reconnect attempts are bounded (10 by default); after exhaustion, the status becomes `failed` and a
  process/daemon restart is required.
- Servers with no resources still have the `read_resource` helper available; it reports when no
  configured server can serve the requested URI.

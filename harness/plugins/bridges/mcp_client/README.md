# bridge-mcp-client

## Summary

Connects external Model Context Protocol servers over stdio, discovers their tools and registers each
one as a native Harness tool named `mcp__<server>__<tool>`. Calls cross the normal gate, so a server
tool is subject to the same permission mode, approvals, jail and redaction as a built-in one. Servers
that fail to start are logged and skipped; they never abort the boot.

## Configuration

<!-- GENERATED table — `harness catalog readme bridge-mcp-client` -->
| Key | Default | Meaning |
|---|---|---|
| `plugins.bridge-mcp-client.servers` | `{}` | External MCP servers, keyed by server name. Tools appear as `mcp__<server>__<tool>`. `plugins.mcp-client.servers` is accepted as a legacy spelling. |

```json
{"plugins": {"bridge-mcp-client": {"servers": {
  "obsidian": {"command": "uvx", "args": ["mcp-obsidian"], "env": {"OBSIDIAN_API_KEY": "..."}}
}}}
```

An MCP server over stdio is the way to reach Obsidian today (a `mcp-obsidian`-style server speaking to
the Local REST API). A `settings.json` written by hand is read at boot; the settings panel edits the
same key.

## Extension points

- Registers: `tool` provider for every discovered server tool, plus `list_resources` / `read_resource`
  when a server advertises resources.
- Replaces: nothing. Two plugins can register the same server name; the second is rejected with
  `Tool already registered` (see `registry.register_tool`).
- Consumes: `harness.sdk.contracts.ToolHandler` semantics on the way out, `MCPClient` (`client.py`) on
  the way in — the JSON-RPC transport is a plain stdio loop, so an HTTP transport (WS-09) is additive.

## Model experience

Tool names and descriptions come verbatim from the server's `tools/list`, so what the model sees is what
the server author wrote. Results are the server's text content blocks joined with newlines; images are
passed through as media. Risk class is derived from the server's `ToolAnnotations`:
`readOnlyHint` → `read`, `destructiveHint` → `destructive`, `openWorldHint` → `network`, and an
unannotated tool → `write` (which means approval is required in `ask` mode — an unlabelled tool is not
trusted, it is treated as a mutation).

## Known limitations

- stdio only: a server that speaks Streamable HTTP cannot be configured yet (WS-09), which is what
  most hosted Obsidian bridges expose.
- No reconnect: if a server process dies mid-session its tools keep being offered and the next call
  fails; there is no backoff or resubscribe (WS-09).
- `tools/list` is read once at boot; a server that changes its tool list needs a daemon restart.

# Search Tools Plugin (`search-tools`)

Provides regex codebase search (`search_codebase`):
- Uses ripgrep binary (`rg`) via `ctx.executor` when available for maximum speed.
- Automatically falls back to a pure-Python regex search engine with identical semantics when `rg` is unavailable or without `exec` capability.
- Supports case sensitivity, glob filtering, result capping, and offset-based pagination.

# Repo Map Context Source (`context.repo_map`)

Provides an automated codebase symbol outline in the `SESSION` stability tier:
- Parses Python files via AST and other source code files (TypeScript, Go, Rust, Java, C++) via structural regex.
- Enforces token budget bounds (`context.repo_map.max_tokens`, default 1024).
- Skips mapping when codebase exceeds 2,000 files unless `context.repo_map.force=true`.
- Uses fast workspace fingerprinting to avoid re-generating map when codebase files are unchanged.

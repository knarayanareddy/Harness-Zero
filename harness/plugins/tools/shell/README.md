# Shell Tools Plugin (`shell-tools`)

Provides system command execution and background task management:
- `run_command`: Runs commands in the workspace via `ctx.executor`, streams `tool.output.delta` events, and automatically backgrounds jobs when runtime exceeds `wait_ms_before_async`.
- `manage_task`: Inspects, lists, waits for, sends input to, or kills background tasks.

# Local Executor Plugin (`local-executor`)

Default process executor for Harness-Zero running commands directly on the host system within workspace jail boundaries.

## Features
- **Process Group Isolation**: Spawns processes in isolated process groups (`os.setsid`) to guarantee cleanup of descendant subprocess trees.
- **Environment Scrubbing**: Whitelists safe environment variables (`PATH`, `HOME`, `LANG`, etc.) preventing credential leakage from parent environment.
- **Output Streaming**: Streams stdout and stderr chunks in real time without blocking.
- **Graceful Termination**: Sends `SIGTERM` first, allowing graceful shutdown before escalating to `SIGKILL`.

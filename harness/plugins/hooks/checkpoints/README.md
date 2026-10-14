# Cline-style Checkpoints (`hooks.checkpoints`)

Provides automated workspace rollbacks and shadow git snapshots:
- Maintains shadow git repository at `<workspace>/.harness/checkpoints/.git`.
- Subscribes `CheckpointHook` to `PRE_TOOL_USE` on state-mutating tools (`write_to_file`, `edit_file`, `run_command`, `create_artifact`).
- Provides `checkpoint_restore` tool allowing users or agents to revert workspace changes.

# Architect/Editor Strategy (`strategies.architect_editor`)

Implements a two-phase architecture and coding loop:
1. **Architect Phase**: Analyzes context and formulates surgical implementation steps.
2. **Editor Phase**: Applies file-headed SEARCH/REPLACE diff blocks via `edit_file` tool so edits are audited, jailed, and carded.
3. **Verification**: Executes optional verification test command.
4. **Safe Staging**: Staging touches ONLY modified files (`git add -- <files>`), never dirty workspace files (`git add -A`), and refuses when index is not clean.

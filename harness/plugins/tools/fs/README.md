# Filesystem Tools Plugin (`fs-tools`)

Core filesystem tools for Harness-Zero: `view_file`, `write_to_file`, and `edit_file`.

## Tools Provided
- **`view_file`**: Reads file content with optional 1-indexed `start_line` and `end_line` slicing. Declared risk: `READ`.
- **`write_to_file`**: Creates or completely overwrites a file. Declared risk: `WRITE`.
- **`edit_file`**: Performs surgical in-place edits using `<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE` blocks. Emits timeline diff cards and records `changed_files`. Declared risk: `WRITE`.

# Repeat Call Reminder (`hook-repeat-call-reminder`)

## Summary
Counts identical `(tool, arguments)` calls inside one turn and, from `repeat_after` on, appends a note
to the result telling the model it is repeating itself. A turn that keeps re-running the same command
and re-reading the same output costs a model step and a screenful of context per repetition; the
cheap fix is to make the repetition visible.

## Configuration
| Key | Default | Effect |
| --- | --- | --- |
| `plugins.hook-repeat-call-reminder.enabled` | `true` | Master switch. |
| `plugins.hook-repeat-call-reminder.repeat_after` | `3` | Count that triggers the note. |
| `plugins.hook-repeat-call-reminder.block_after` | `0` | Count that blocks the call; `0` disables blocking. |
| `plugins.hook-repeat-call-reminder.exempt_tools` | `["manage_task", "view_file"]` | Polling and paging are not spins. |

## Extension points
One `POST_TOOL_USE` hook. State is `hook_ctx.turn_state["repeat_calls"]`, which the kernel scopes to
one turn: no module globals, nothing shared between sessions, nothing left behind by a restart.
Publishes the live-only event `tool.repeat_detected`.

## Model experience
Appended block, exactly:

```
Note: this is the 3rd identical call to run_command with the same arguments in this turn. The result has not changed. Consider a different approach or explain why repeating is needed.
```

The claim is checked, not assumed: the counter only increments while the *result* is byte-identical,
so re-running `pytest` after an edit — same command, new output — does not nag. With `block_after` set
the call is replaced by `Blocked by post-tool hook: …`, which reads as an error to the model and to
the UI.

## Known limitations
* "Identical" means identical `name` + `arguments` JSON; a call that differs by one whitespace
  character is a different call. A spin that rephrases its command is invisible here (WS-04's loop
  detector is the right place for that).
* Counters are per turn on purpose: a model that reads the same file across three turns is working,
  not spinning.

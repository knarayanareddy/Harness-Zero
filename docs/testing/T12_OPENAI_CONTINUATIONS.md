# T12 — OpenAI-compatible tool continuations

Status: locally verified. G1/global qualification remains non-green; no live
vendor request, release, commit or push is implied.

The canonical SDK uses USER-role messages containing ToolResultBlock entries.
The OpenAI adapter previously converted these to empty ordinary user messages,
losing the tool results. It now emits one `role: tool` message for each result,
preserving its tool_call_id and text. Legacy TOOL-role batches still work.
Result-only batches no longer create a fake empty user turn; mixed ordinary
text/images follow the tool messages without being dropped. Conversion does not
mutate the canonical request or change other adapters' stored representations.

Chat Completions has no tool-message is_error field. Errors are represented by
the explicit textual marker `[Tool error]\n` plus their original text, rather
than adding an undocumented JSON field or implying successful tool execution.
This slice qualifies **text tool results**, not tool-result images, audio or every
OpenAI-compatible vendor/model. T13/T22 still own media contract propagation.

## Primary reference

Read 2026-10-22: OpenAI's generated SDK type
https://github.com/openai/openai-python/blob/main/src/openai/types/chat/chat_completion_tool_message_param.py
declares required `role: tool`, `tool_call_id`, and text content. This establishes
the payload shape, not account access, live compatibility or pricing.

## Evidence (`.venv/evidence/`, ignored)

- `t12-unit-red.xml`: canonical USER results are lost; legacy errors lack an
  explicit failure marker. Both fail before the change.
- `t12-wire-baseline-red.xml`: HEAD's original conversion method was injected
  in memory into the current adapter class; the actual local HTTP peer rejected
  the real Commands/runner/gate/store continuation with HTTP 400 missing results.
  No checkout rollback or substitution of the gate/tool executor was used.
- `t12-final-py312.xml`: **9 passed in 45.27s**.
- `t12-final-py311.xml`: **9 passed in 20.65s**.
- `t12-r02.xml`: original R02 passes; 18 unrelated findings deselected.
- Luna's independent source-only review found no actionable issue. It did not
  independently execute these tests or qualify pending media/vendor behavior.

The actual adapter talks over local TCP HTTP/SSE to an independent strict peer.
Two actual READ tool handlers return success and failure through the real gate;
the second request contains both IDs and their expected bodies. A reconstructed
store/log derives the same message sequence and the peer accepts its replay.
Removing one result makes that peer reject the request with HTTP 400. Unit
controls also preserve mixed user notes/images, legacy TOOL batches and unchanged
canonical SDK data. No golden, recording, assertion or timeout was weakened.

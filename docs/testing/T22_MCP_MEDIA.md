# T22 — preserve MCP tool images through a supported adapter

Status: locally integrated, tested and independently source-reviewed. G1 and broader
live/artifact qualification remain non-green.

The MCP tool handler now preserves ordered text/image content using canonical
ImageBlock values. Missing/unsupported/malformed content produces explicit errors
instead of placeholder Success. Base64 and MIME fields are validated; images are
limited to 48 KiB aggregate encoded data per result. No URL or local-file fetch
is introduced. This is a post-parse retention/processing bound, not a transport
buffering limit or decoded-image authenticity check.

## Parent evidence (`.venv/evidence/`, local/ignored)

- Worker mixed-content fail-first and image-only saved-base fail-first both
  expose the missing image behavior. The rate-limited dispatch was not counted
  as completion; partial files/evidence were preserved before a fresh child
  validated and strengthened the candidate. A preliminary import-helper attempt
  did not exercise the saved base and is not counted as fail-first evidence.
- The real local stdio peer advertises separate mixed and image-only tools.
  The actual registry/gate/runner persists canonical results, and the next
  Anthropic adapter request preserves image-only and mixed-order content. A new
  TranscriptLog backed by a new JsonlTranscriptStore reloads the records; its
  messages then drive another actual AnthropicProvider.generate call.
- The HTTP requests use strict httpx.MockTransport body checks, not a socket
  peer or vendor API. Known text secrets are absent from persisted/replayed
  content. This does not claim OCR or binary-secret redaction.
- Worker final focused client/media results: five passed per Python version.
  Parent destination hashes match the worker seed before integration.
- Review found an avoidable allocation before the size check. Parent's two
  copy-observer probes fail first for a single oversize image and for exceeding
  the remaining aggregate budget (`t22-parent-allocation-red.xml`). The check now
  precedes ASCII encoding; only potentially fitting input is copied/decoded.
- Parent preserves the existing optional cancel_event helper compatibility.
  Changing it to strict attribute access broke unchanged original R12's minimal
  context. Real ToolContext events still propagate, and the original probe is
  not modified to manufacture a pass.
- Re-review found zero-length typed/legacy text bypassing the empty-content
  error. Three empty-only probes and a whitespace-preserving positive control
  fail first (`t22-parent-empty-text-red.xml`); zero-length blocks are now skipped
  without stripping nonempty text or disturbing image order.
- Final combined client/kernel/media/OpenAI subset: **67 passed on each Python
  version** (`t22-parent-empty-fixed-py312.xml` and `...-py311.xml`). Original
  R02/R06/R11/R12: **four passed per version** after the last fix.
  Ruff and diff whitespace pass. Earlier pre-compatibility results remain
  historical; their R12 failure is not concealed.

The parent candidate snapshot is
`78abd43a1b8b33c2195ee6c452fc3111dc54f513f71c6168af98e9b5f8fe213d`;
its saved-base-bound patch hash is
`1543d23486a0e812cbd2f71e520f90f101c0ea4503b206ffe96e7ffaeca62b38`.
Luna's final source review independently verifies that snapshot and actual patch
hash and reports no remaining findings. The reviewer does not execute tests.
Parent rechecks both before accepting; see `t22-acceptance-receipt.json`. A later
combined T18b/T22 subset passes **92 checks on each Python version**; this is
still not a full-suite result.

## Adapter and qualification limits

Anthropic is the capable serializer exercised here. The parent-owned
OpenAI-compatible Chat Completions guard rejects image-bearing tool results
before HTTP with a non-retryable bad_request, preserving ordinary user images
and text tool results; eleven controls pass per version and its separate exact
snapshot is source-reviewed. This is an adapter limitation, not an assertion
that all OpenAI endpoints/models lack tool-media support. Gemini/Ollama's
currently unsupported tool-image modes retain T13's explicit rejection.

No live model, arbitrary external MCP host, whole-image privacy guarantee,
transport memory ceiling, full-suite pass or packaged artifact is qualified by
these checks. MCP resource reads are a separate path, not repaired by the tool
result serializer. T18 audit and cancellation acceptance remain separately owned.

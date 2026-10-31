# Anthropic Messages Provider Plugin (`provider-anthropic`)

Direct adapter for the Anthropic Messages API (`/v1/messages`):
- Ephemeral prompt caching (`cache_control: {"type": "ephemeral"}`) at STATIC tier boundaries and tool catalogs (≤ 4 breakpoints).
- Native tool calling with streamed partial JSON arguments (`input_json_delta` -> `ToolCallEnd`).
- Reasoning/thinking block streaming (`thinking_delta` for Claude 3.7 Sonnet).
- Exact cache read and cache creation token accounting (`cache_read_input_tokens`, `cache_creation_input_tokens`).
- Normalized error taxonomy mapping without leaking raw transport exceptions.
- HTTP-200 SSE error events and premature EOF are failures, not successful completions; cumulative output usage is counted once. Local peer/runner/fragmentation evidence and remaining limits: `docs/testing/T14_ANTHROPIC_STREAMS.md`.
- Image serialization uses SDK base64/HTTPS URL sources and preserves nested image tool results. Endpoint-specific modes and unverified live/model/size limits: `docs/testing/T13_PROVIDER_MEDIA.md`.

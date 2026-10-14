# Anthropic Messages Provider Plugin (`provider-anthropic`)

Direct adapter for the Anthropic Messages API (`/v1/messages`):
- Ephemeral prompt caching (`cache_control: {"type": "ephemeral"}`) at STATIC tier boundaries and tool catalogs (≤ 4 breakpoints).
- Native tool calling with streamed partial JSON arguments (`input_json_delta` -> `ToolCallEnd`).
- Reasoning/thinking block streaming (`thinking_delta` for Claude 3.7 Sonnet).
- Exact cache read and cache creation token accounting (`cache_read_input_tokens`, `cache_creation_input_tokens`).
- Normalized error taxonomy mapping without leaking raw transport exceptions.

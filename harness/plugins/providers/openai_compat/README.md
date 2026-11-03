# OpenAI-Compatible Provider Plugin (`openai_compat`)

Universal inference provider supporting OpenAI, OpenRouter, DeepSeek, and local Ollama or vLLM endpoints.

## Features
- **Streaming SSE**: Delivers `model.text.delta` and `model.thinking.delta` in real-time.
- **Native Tool Calling**: Streams argument chunks and accumulates typed `ToolCallEnd` blocks.
- **Text Tool Continuations**: Converts canonical USER-carried results into matching `role: tool` messages, including explicit textual error markers, while retaining ordinary user notes/images.
- **Cache Statistics**: Extracts and tracks prompt cache hit tokens (`prompt_tokens_details.cached_tokens`).
- **Reasoning Models**: Transmits `reasoning_effort` for OpenAI o1/o3 and parses reasoning tokens for DeepSeek R1.
- **Resilience**: Maps network and HTTP status codes cleanly into typed `ProviderError` without raw client exceptions.

## Configuration
Secrets checked from `Secrets` store:
- `openai_api_key` / `OPENAI_API_KEY`
- `openrouter_api_key` / `OPENROUTER_API_KEY`
- `deepseek_api_key` / `DEEPSEEK_API_KEY`

## Verification limits

### Native OpenAI Chat Completions profiles

For the exact `gpt-6-luna` and `gpt-6.1-terra` IDs at `api.openai.com`, an unset
reasoning mode defaults to `none`, the compatible tool-calling mode observed on
that endpoint. SDK `max_tokens` limits are serialized as `max_completion_tokens`.
Conflicting token limits or an explicit non-`none` tool-reasoning mode fail before
the request, rather than silently changing the user's setting. Text-only explicit
reasoning settings remain intact. These defaults do not change OpenRouter,
DeepSeek, local/proxy servers, legacy models or unverified aliases.

Provider timeouts remain bounded and are not automatically replayed. The turn
retains the provider's retryable classification only if it has not admitted tool
work; once tool effects are possible, replaying the entire turn is not marked
safe. A subsequent explicit turn can continue from the durable history. This does
not guarantee remote service availability or undo an already completed action.

The Commands -> gate -> transcript -> adapter -> strict local HTTP/SSE peer round trip is tested with multiple text results, errors, reload and an intentionally missing-result rejection. This is not a live compatibility claim for every listed vendor/model. End-to-end tool-result media propagation remains T22; T13's separate adapter matrix is in `docs/testing/T13_PROVIDER_MEDIA.md`. See `docs/testing/T12_OPENAI_CONTINUATIONS.md` for the text-continuation evidence.

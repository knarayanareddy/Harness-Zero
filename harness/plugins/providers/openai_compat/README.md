# OpenAI-Compatible Provider Plugin (`openai_compat`)

Universal inference provider supporting OpenAI, OpenRouter, DeepSeek, and local Ollama or vLLM endpoints.

## Features
- **Streaming SSE**: Delivers `model.text.delta` and `model.thinking.delta` in real-time.
- **Native Tool Calling**: Streams argument chunks and accumulates typed `ToolCallEnd` blocks.
- **Cache Statistics**: Extracts and tracks prompt cache hit tokens (`prompt_tokens_details.cached_tokens`).
- **Reasoning Models**: Transmits `reasoning_effort` for OpenAI o1/o3 and parses reasoning tokens for DeepSeek R1.
- **Resilience**: Maps network and HTTP status codes cleanly into typed `ProviderError` without raw client exceptions.

## Configuration
Secrets checked from `Secrets` store:
- `openai_api_key` / `OPENAI_API_KEY`
- `openrouter_api_key` / `OPENROUTER_API_KEY`
- `deepseek_api_key` / `DEEPSEEK_API_KEY`

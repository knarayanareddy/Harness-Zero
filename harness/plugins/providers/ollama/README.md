# Ollama Provider Plugin (PLG-12)

Local-first Ollama provider with dual-mode tool invocation:
1. **Native Tool Calling**: Tries native OpenAI-compatible tool calling first via `/v1/chat/completions`.
2. **Automatic XML Fallback**: On HTTP 400 (unsupported tools / format error), retries once with Alibaba Qwen / DeepSeek-style XML `<tool_call>` protocol.
3. **Reasoning Stream Parsing**: Automatically de-multiplexes `<think>...</think>` tags into `ThinkingDelta` events.
4. **Model Discovery**: Dynamically queries `/api/tags` to list installed local models.

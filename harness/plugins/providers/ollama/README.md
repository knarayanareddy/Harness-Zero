# Ollama Provider Plugin (PLG-12)

Local-first Ollama provider with dual-mode tool invocation:
1. **Native Tool Calling**: Tries native OpenAI-compatible tool calling first via `/v1/chat/completions`.
2. **Automatic XML Fallback**: On HTTP 400 (unsupported tools / format error), retries once with Alibaba Qwen / DeepSeek-style XML `<tool_call>` protocol.
3. **Reasoning Stream Parsing**: Automatically de-multiplexes `<think>...</think>` tags into `ThinkingDelta` events.
4. **Model Discovery**: Dynamically queries `/api/tags` to list installed local models.

Image inputs use the documented OpenAI-compatible inline data-URI string for PNG/JPEG; external image URLs and image tool results are rejected by this adapter. See `docs/testing/T13_PROVIDER_MEDIA.md`. XML closing-tag fragmentation, malformed calls, retained-size bounds and benign trailing-text controls are covered in `docs/testing/T16_XML_FRAGMENTATION.md`; these are local adapter/parser checks, not live-model qualification.

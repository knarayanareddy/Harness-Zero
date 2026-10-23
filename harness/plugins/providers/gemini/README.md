# Gemini Provider Plugin (PLG-11)

Native integration for Google Gemini models using the `streamGenerateContent` API with native function calling and usage reporting:

- **System Instruction**: Formatted as `systemInstruction: { parts: [{ text: ... }] }`.
- **Tools**: Formatted as `tools: [{ functionDeclarations: [...] }]`.
- **Turn Merging**: Strict role alternation between `user` and `model`; consecutive same-role turns are automatically merged.
- **Function Calling**: Full bidirectional translation between `functionCall`/`functionResponse` and Harness-Zero `ToolUseBlock`/`ToolResultBlock`.
- **Header Auth**: Passes API key via `x-goog-api-key` request header (never in query parameters).
- **Usage Metrics**: Extracts and normalizes tokens from `usageMetadata`.
- **Transport Failures**: Connection and mid-stream HTTPX failures use valid SDK timeout/unavailable classifications; cancellation stays distinct. Tested through local peers and the real runner, not live Gemini. See `docs/testing/T15_GEMINI_TRANSPORT.md`.
- **Image Inputs**: SDK inline base64 and documented HTTPS fileData modes are serialized without re-encoding; validation uses the effective wire model, including the 2.0 URL restriction. Tool-result images remain unsupported by this adapter. Scope and live-model limitations: `docs/testing/T13_PROVIDER_MEDIA.md`.

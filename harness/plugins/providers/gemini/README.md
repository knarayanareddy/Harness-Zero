# Gemini Provider Plugin (PLG-11)

Native integration for Google Gemini models using the `streamGenerateContent` API with native function calling and usage reporting:

- **System Instruction**: Formatted as `systemInstruction: { parts: [{ text: ... }] }`.
- **Tools**: Formatted as `tools: [{ functionDeclarations: [...] }]`.
- **Turn Merging**: Strict role alternation between `user` and `model`; consecutive same-role turns are automatically merged.
- **Function Calling**: Full bidirectional translation between `functionCall`/`functionResponse` and Harness-Zero `ToolUseBlock`/`ToolResultBlock`.
- **Header Auth**: Passes API key via `x-goog-api-key` request header (never in query parameters).
- **Usage Metrics**: Extracts and normalizes tokens from `usageMetadata`.

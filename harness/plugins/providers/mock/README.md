# Mock Provider Plugin (PLG-13)

Deterministic offline mock provider:
- Absorbs MDL-01 and Decision D1.
- By default, echoes `[mock] <prompt>` with zero simulated tool calls and valid usage metrics.
- Supports deterministic scenario replay via `--mock-script <path.json>` or `providers.mock.script_file` setting.
- Built on top of `ScriptedProvider` for conformance and hermetic testing.

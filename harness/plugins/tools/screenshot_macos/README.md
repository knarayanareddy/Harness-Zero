# macOS Screenshot Tool (`tools.screenshot_macos`)

Provides the `take_screenshot` tool:
- Captures active desktop screen cleanly using `screencapture` executed through the registered executor.
- Encodes output image as base64 and returns a native `ImageBlock`.
- Registered strictly on `sys.platform == "darwin"` when `screencapture` is available in PATH.
- Deleted fake visual-grounding UI-TARS heuristics (SIM-03).

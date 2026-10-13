---
name: desktop-automator
description: Desktop and OS automation via macOS Accessibility, screen captures, keyboard/mouse synthesis, and UI-TARS visual grounding.
version: 1.0.0
---

# Desktop Automator Playbook

## Purpose
Use this skill for automated desktop interaction, GUI application verification, screen captures, and operating system control.

## Methodology
1. **Query Accessibility Tree**: Inspect AXUIElement window and element hierarchy.
2. **Visual Verification**: Take a screenshot via ScreenCaptureKit to confirm element states.
3. **Execute Action**: Synthesize keystrokes, clicks, or menu activations.
4. **Visual Fallback**: Fallback to visual coordinate grounding (UI-TARS pattern) for Canvas or custom WebGL components.

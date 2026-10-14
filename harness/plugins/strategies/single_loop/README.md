# Single Loop Strategy Plugin (`strategy-single-loop`)

Provides the default agent execution loop:
- Mediates all model calls, tool execution, and transcript recording through `KernelServices`.
- Enforces budget-triggered compaction before each model call.
- Provides reactive suspension and wake-up on background task exits (0% busy-wait polling).
- Supports STOP-hook re-entry (up to 2 iterations) when verification hooks block completion.

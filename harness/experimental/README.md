# Harness-Zero Experimental Modules

This directory contains experimental and prototype modules quarantined per **EXP-01** (Decision D2). These components are isolated from the active kernel, SDK, and production plugins, awaiting future integration or modular plugin rewrites:

## Quarantined Components

1. **`architect_editor.py`**:
   - *Status*: Legacy prototype.
   - *Reason*: Superseded by the modular plugin strategy at `harness/plugins/strategies/architect_editor`.

2. **`battle_arena.py`**:
   - *Status*: Quarantined prototype (ORCH-02).
   - *Reason*: Spawns parallel competing candidate solutions in isolated Git worktrees. Quarantined pending full `KernelServices.spawn_child` coordinator integration.
   - *Changes*: Simulated default worker removed (requires explicit `worker_fn`).

3. **`spawn_ledger.py`**:
   - *Status*: Quarantined prototype (ORCH-04).
   - *Reason*: Hierarchical subagent tree tracking and concurrency/depth enforcement. Quarantined until a first-class `spawn_subagent` kernel tool and coordinator exists.

4. **`state_graph.py`**:
   - *Status*: Quarantined prototype (ORCH-04).
   - *Reason*: Cyclic state graph workflow execution and checkpointing. Quarantined pending modular orchestration plugin architecture.

5. **`concurrency_gate.py`**:
   - *Status*: Quarantined prototype (ORCH-04).
   - *Reason*: Task queuing and concurrent slot allocation. Quarantined pending background worker pool integration.

6. **`browser_cdp.py`** (formerly `browser/stagehand_engine.py`):
   - *Status*: Quarantined prototype (SIM-02).
   - *Reason*: Browser automation via Chrome DevTools Protocol (CDP). Quarantined pending a verified headless Chrome lifecycle manager.
   - *Changes*: Silent fallback to mock mode removed in `connect()`. If no active CDP endpoint responds, `connect()` fails honestly (`return False`).

## Import Policy

Per `tests/kernel/test_import_boundaries.py`:
- Active kernel code (`harness/kernel/**`) and plugins (`harness/plugins/**`) must **NEVER** import from `harness.experimental`.
- Experimental modules are maintained solely for research, testing, and future plugin ports.

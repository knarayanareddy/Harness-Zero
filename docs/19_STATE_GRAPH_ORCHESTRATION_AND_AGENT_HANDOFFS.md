# 19: State Graph Orchestration & Agent Handoffs
*LangGraph State Graphs, Persistent Checkpointers, OpenAI Agent Handoffs, and Claude Code Tool Scoping*

---

## 1. Beyond Flat Multi-Agent Loops

While hierarchical spawn trees ([`15_HERMES_MULTI_AGENT_AND_TIRITH_SECURITY.md`](./15_HERMES_MULTI_AGENT_AND_TIRITH_SECURITY.md)) manage worker lifecycles, complex production workflows require **Explicit Graph Routing**, **Time-Travel Checkpointing**, and **Specialist Handoffs**:

```mermaid
flowchart TD
    START((Start)) --> TRIAGE[Triage Agent]

    TRIAGE -->|Handoff: Coding Task| ARCHITECT[Architect Agent Node]
    TRIAGE -->|Handoff: Bug Report| REPRO[Reproduction Node]

    ARCHITECT --> CODER[Coder Agent Node]
    CODER --> TESTER[Test Verification Node]

    TESTER -->|Tests Fail (Cycle)| CODER
    TESTER -->|Tests Pass| CHECKPOINT[(State Checkpointer)]
    CHECKPOINT --> HUMAN{Human Approval Gate}
    HUMAN -->|Approved| COMMIT[Git Auto-Commit Node]
    HUMAN -->|Rejected| ARCHITECT
    COMMIT --> END((End))
```

---

## 2. Reverse-Engineered Orchestration Patterns

### A. LangGraph Cyclic Graphs & Time-Travel Checkpointing
* **StateGraph**: The execution flow is modeled as a directed graph where nodes mutate a typed shared state dictionary, and conditional edges evaluate routing predicates.
* **Persistent Checkpointer**: Saves every state transition into SQLite or PostgreSQL.
  * **Time-Travel Debugging**: An agent or human can rewind the conversation to step 4, alter the plan, and fork a new branch of execution without repeating steps 1–3.
* **Human-in-the-Loop Interrupts**: Execution yields control before entering high-risk nodes (e.g. database migrations, external git pushes), resuming only upon explicit human approval.

### B. OpenAI Agents SDK Handoff Pattern
* **Transferring Ownership**: Instead of always routing answers back to a coordinator agent, a specialist agent executes a **Handoff**:
  ```python
  def transfer_to_database_specialist():
      return database_agent  # Control immediately transitions to database_agent
  ```
* Eliminates token-wasting intermediate summarization turns.

### C. Claude Code Subagent Scoping
* **Fine-Grained Tool Whitelisting**:
  * Root Coordinator: Has `invoke_subagent`, `ask_question`, `read_file`.
  * Researcher Subagent: Scoped strictly to `search_web`, `read_url_content`, and read-only MCP servers.
  * Coding Subagent: Scoped strictly to `edit_file`, `run_command`, `git_commit`.
* Prevents researcher subagents from accidentally modifying local code.

---

## 3. Custom Harness Implementation (Python StateGraph)

```python
"""
state_graph_orchestrator.py - Cyclic StateGraph with Checkpointing and Handoffs
Clean-room implementation of LangGraph and OpenAI Agents SDK architecture.
"""
from dataclasses import dataclass, field
from typing import Callable, Any
import copy
import json
import sqlite3

@dataclass
class AgentState:
    messages: list[dict] = field(default_factory=list)
    current_agent: str = "triage"
    iteration_count: int = 0
    test_results: dict[str, Any] = field(default_factory=dict)
    is_complete: bool = False

class StateGraphEngine:
    def __init__(self, checkpointer_db: str = ":memory:"):
        self.nodes: dict[str, Callable[[AgentState], AgentState]] = {}
        self.conditional_edges: dict[str, Callable[[AgentState], str]] = {}
        self.conn = sqlite3.connect(checkpointer_db)
        self.conn.execute("CREATE TABLE IF NOT EXISTS checkpoints (step INTEGER PRIMARY KEY, state_json TEXT);")

    def add_node(self, name: str, func: Callable[[AgentState], AgentState]):
        self.nodes[name] = func

    def add_conditional_edge(self, source_node: str, router_func: Callable[[AgentState], str]):
        self.conditional_edges[source_node] = router_func

    async def execute(self, initial_state: AgentState, max_steps: int = 10) -> AgentState:
        state = copy.deepcopy(initial_state)
        current_node_name = state.current_agent

        for step in range(1, max_steps + 1):
            if state.is_complete:
                break

            node_func = self.nodes.get(current_node_name)
            if not node_func:
                raise ValueError(f"Unknown node {current_node_name}")

            # Execute node
            state = await node_func(state) if asyncio.iscoroutinefunction(node_func) else node_func(state)

            # Checkpoint state (Enables time-travel rollback)
            with self.conn:
                self.conn.execute("INSERT OR REPLACE INTO checkpoints VALUES (?, ?)", (step, json.dumps(state.__dict__)))

            # Route to next node
            if current_node_name in self.conditional_edges:
                current_node_name = self.conditional_edges[current_node_name](state)
            else:
                break

        return state
```

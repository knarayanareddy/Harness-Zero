"""
Cyclic StateGraph Engine with Time-Travel Checkpointing & Specialist Handoffs.
Clean-room implementation synthesized from LangGraph and OpenAI Agents SDK.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Union


@dataclass
class AgentState:
    messages: List[Dict[str, Any]] = field(default_factory=list)
    current_agent: str = "triage"
    iteration_count: int = 0
    test_results: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    is_complete: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "messages": self.messages,
            "current_agent": self.current_agent,
            "iteration_count": self.iteration_count,
            "test_results": self.test_results,
            "artifacts": self.artifacts,
            "is_complete": self.is_complete,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentState:
        return cls(
            messages=data.get("messages", []),
            current_agent=data.get("current_agent", "triage"),
            iteration_count=data.get("iteration_count", 0),
            test_results=data.get("test_results", {}),
            artifacts=data.get("artifacts", {}),
            is_complete=data.get("is_complete", False),
            metadata=data.get("metadata", {}),
        )


class StateGraphEngine:
    def __init__(self, checkpointer_db: Union[str, Path] = ":memory:"):
        self.nodes: Dict[str, Callable[[AgentState], Any]] = {}
        self.conditional_edges: Dict[str, Callable[[AgentState], str]] = {}
        self.conn = sqlite3.connect(str(checkpointer_db), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    step INTEGER PRIMARY KEY,
                    agent_node TEXT,
                    state_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def add_node(self, name: str, func: Callable[[AgentState], Any]) -> None:
        self.nodes[name] = func

    def add_conditional_edge(
        self, source_node: str, router_func: Callable[[AgentState], str]
    ) -> None:
        self.conditional_edges[source_node] = router_func

    async def execute(
        self,
        initial_state: AgentState,
        max_steps: int = 15,
    ) -> AgentState:
        """Executes the cyclic graph state machine with time-travel checkpoints."""
        state = copy.deepcopy(initial_state)
        current_node_name = state.current_agent

        for step in range(1, max_steps + 1):
            if state.is_complete:
                break

            node_func = self.nodes.get(current_node_name)
            if not node_func:
                raise ValueError(f"Unknown graph node: '{current_node_name}'")

            state.iteration_count = step
            state.current_agent = current_node_name

            # Execute node (async or sync)
            if asyncio.iscoroutinefunction(node_func):
                state = await node_func(state)
            else:
                state = node_func(state)

            # Persist checkpoint to SQLite (enables time-travel rollback)
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO checkpoints (step, agent_node, state_json) VALUES (?, ?, ?)",
                    (step, current_node_name, json.dumps(state.to_dict())),
                )

            # Route to next node via conditional edge
            if current_node_name in self.conditional_edges:
                next_node = self.conditional_edges[current_node_name](state)
                if next_node == "__END__":
                    state.is_complete = True
                    break
                current_node_name = next_node
            else:
                break

        return state

    def rollback_to_step(self, step: int) -> Optional[AgentState]:
        """Time-Travel Debugging: restore agent state from historical step checkpoint."""
        cursor = self.conn.execute(
            "SELECT state_json FROM checkpoints WHERE step = ?", (step,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        return AgentState.from_dict(data)

    def close(self) -> None:
        self.conn.close()

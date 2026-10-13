"""
Hermes-Style Hierarchical Spawn Trees & Subagent Ledger.
Tracks parent-child task hierarchies, prevents runaway recursion via nesting depth limits,
and persists state machine transitions to SQLite WAL and spawn-ledger.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import uuid


class SubagentStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    KILLED = "KILLED"


@dataclass
class SpawnRecord:
    task_id: str
    parent_id: Optional[str]
    agent_name: str
    role: str
    goal: str
    nesting_depth: int
    status: SubagentStatus = SubagentStatus.PENDING
    worktree_path: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: Optional[str] = None
    result_summary: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "parent_id": self.parent_id,
            "agent_name": self.agent_name,
            "role": self.role,
            "goal": self.goal,
            "nesting_depth": self.nesting_depth,
            "status": self.status.value,
            "worktree_path": self.worktree_path,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "result_summary": self.result_summary,
            "error_message": self.error_message,
        }


class SpawnLedger:
    def __init__(self, session_dir: Path, max_nesting_depth: int = 3):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.max_nesting_depth = max_nesting_depth
        self.ledger_file = self.session_dir / "spawn-ledger.json"
        self.db_path = self.session_dir / "spawn_state.db"

        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS spawn_tree (
                    task_id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    agent_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    nesting_depth INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    worktree_path TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    result_summary TEXT,
                    error_message TEXT
                );
                """
            )

    def spawn_subagent(
        self,
        agent_name: str,
        role: str,
        goal: str,
        parent_id: Optional[str] = None,
        worktree_path: Optional[str] = None,
    ) -> SpawnRecord:
        parent_depth = 0
        if parent_id:
            parent = self.get_task(parent_id)
            if parent:
                parent_depth = parent.nesting_depth

        nesting_depth = parent_depth + 1
        if nesting_depth > self.max_nesting_depth:
            raise RecursionError(
                f"Subagent nesting depth limit ({self.max_nesting_depth}) exceeded by task for {agent_name}"
            )

        task_id = f"sub_{uuid.uuid4().hex[:8]}"
        record = SpawnRecord(
            task_id=task_id,
            parent_id=parent_id,
            agent_name=agent_name,
            role=role,
            goal=goal,
            nesting_depth=nesting_depth,
            status=SubagentStatus.RUNNING,
            worktree_path=worktree_path,
        )

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO spawn_tree (
                    task_id, parent_id, agent_name, role, goal,
                    nesting_depth, status, worktree_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.task_id,
                    record.parent_id,
                    record.agent_name,
                    record.role,
                    record.goal,
                    record.nesting_depth,
                    record.status.value,
                    record.worktree_path,
                    record.created_at,
                ),
            )

        self._sync_ledger_json()
        return record

    def update_task_status(
        self,
        task_id: str,
        status: SubagentStatus,
        result_summary: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """
                UPDATE spawn_tree
                SET status = ?, finished_at = ?, result_summary = ?, error_message = ?
                WHERE task_id = ?;
                """,
                (status.value, now_str, result_summary, error_message, task_id),
            )
        self._sync_ledger_json()

    update_status = update_task_status

    def get_task(self, task_id: str) -> Optional[SpawnRecord]:
        cursor = self.conn.execute(
            """
            SELECT task_id, parent_id, agent_name, role, goal,
                   nesting_depth, status, worktree_path, created_at,
                   finished_at, result_summary, error_message
            FROM spawn_tree WHERE task_id = ?;
            """,
            (task_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return SpawnRecord(
            task_id=row[0],
            parent_id=row[1],
            agent_name=row[2],
            role=row[3],
            goal=row[4],
            nesting_depth=row[5],
            status=SubagentStatus(row[6]),
            worktree_path=row[7],
            created_at=row[8],
            finished_at=row[9],
            result_summary=row[10],
            error_message=row[11],
        )

    def list_children(self, parent_id: str) -> List[SpawnRecord]:
        cursor = self.conn.execute(
            "SELECT task_id FROM spawn_tree WHERE parent_id = ?;", (parent_id,)
        )
        tasks = []
        for row in cursor.fetchall():
            t = self.get_task(row[0])
            if t:
                tasks.append(t)
        return tasks

    def get_tree(self) -> List[Dict[str, Any]]:
        cursor = self.conn.execute("SELECT task_id FROM spawn_tree WHERE parent_id IS NULL;")
        roots = []
        for row in cursor.fetchall():
            rec = self.get_task(row[0])
            if rec:
                node = rec.to_dict()
                node["children"] = self._build_children_tree(rec.task_id)
                roots.append(node)
        return roots

    def _build_children_tree(self, parent_id: str) -> List[Dict[str, Any]]:
        children = self.list_children(parent_id)
        subnodes = []
        for c in children:
            c_dict = c.to_dict()
            c_dict["children"] = self._build_children_tree(c.task_id)
            subnodes.append(c_dict)
        return subnodes

    def _sync_ledger_json(self) -> None:
        cursor = self.conn.execute("SELECT task_id FROM spawn_tree ORDER BY created_at ASC;")
        all_records = []
        for row in cursor.fetchall():
            rec = self.get_task(row[0])
            if rec:
                all_records.append(rec.to_dict())
        self.ledger_file.write_text(json.dumps(all_records, indent=2), encoding="utf-8")

    def close(self) -> None:
        self.conn.close()

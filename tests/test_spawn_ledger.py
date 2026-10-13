import pytest
import tempfile
from pathlib import Path
from harness.orchestration.spawn_ledger import SpawnLedger, SpawnRecord, SubagentStatus

def test_spawn_ledger_hierarchy_and_limits():
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger = SpawnLedger(session_dir=Path(tmpdir), max_nesting_depth=2)
        
        # Root spawn
        node1 = ledger.spawn_subagent(
            agent_name="Architect",
            role="System Designer",
            goal="Refactor core engine",
            parent_id=None
        )
        assert node1.nesting_depth == 1
        assert node1.status == SubagentStatus.RUNNING
        
        # Child spawn
        node2 = ledger.spawn_subagent(
            agent_name="Editor",
            role="Code Editor",
            goal="Write unit tests",
            parent_id=node1.task_id
        )
        assert node2.nesting_depth == 2
        assert node2.parent_id == node1.task_id

        # Exceeding max depth should raise RecursionError
        with pytest.raises(RecursionError, match="Subagent nesting depth limit"):
            ledger.spawn_subagent(
                agent_name="Runner",
                role="Test Runner",
                goal="Run pytest",
                parent_id=node2.task_id
            )

        # Update status
        ledger.update_status(node2.task_id, SubagentStatus.COMPLETED, result_summary="All tests written")
        retrieved = ledger.get_task(node2.task_id)
        assert retrieved is not None
        assert retrieved.status == SubagentStatus.COMPLETED
        assert retrieved.result_summary == "All tests written"

        # Check tree output
        tree = ledger.get_tree()
        assert len(tree) == 1
        assert tree[0]["task_id"] == node1.task_id
        assert len(tree[0]["children"]) == 1

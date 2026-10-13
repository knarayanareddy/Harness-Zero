import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from harness.orchestration.architect_editor import ArchitectEditorLoop, ArchitectEditorResult
from harness.core.types import ModelResponse

@pytest.mark.asyncio
async def test_architect_editor_loop_self_healing():
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        target_file = workspace / "math_util.py"
        target_file.write_text("def add(a, b):\n    return a - b\n")

        # Mock Model Router
        mock_router = MagicMock()
        
        # Architect response (plan)
        arch_response = ModelResponse(
            content="Plan: change `return a - b` to `return a + b` in add function."
        )

        # Editor response (diff)
        diff_text = """<<<<<<< SEARCH
def add(a, b):
    return a - b
=======
def add(a, b):
    return a + b
>>>>>>> REPLACE"""

        editor_response = ModelResponse(
            content=diff_text
        )

        mock_router.generate = AsyncMock(side_effect=[arch_response, editor_response])

        loop = ArchitectEditorLoop(
            workspace_root=workspace,
            router=mock_router
        )

        result = await loop.execute(
            task_prompt="Fix subtraction bug in add function",
            target_files=[target_file],
            test_command=None,  # No test command = immediate success on diff apply
            max_retries=2,
            auto_commit=False
        )

        assert result.success is True
        assert "Plan:" in result.plan
        assert len(result.iterations) == 1
        assert "return a + b" in target_file.read_text()

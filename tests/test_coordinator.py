from pathlib import Path
import pytest
from harness.core.coordinator import AgentCoordinator
from harness.core.types import ModelResponse, ToolCall
from harness.models.router import ModelRouter


@pytest.mark.asyncio
async def test_coordinator_tool_execution_loop(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    router = ModelRouter()
    # Mock Turn 1: Model calls write_to_file tool
    resp1 = ModelResponse(
        content="I will create the config file now.",
        thinking="User wants a config file.",
        tool_calls=[
            ToolCall(
                id="call_1",
                name="write_to_file",
                arguments={
                    "TargetFile": str(workspace / "config.json"),
                    "CodeContent": '{"version": "1.0", "name": "test"}',
                },
            )
        ],
    )
    # Mock Turn 2: Model finishes turn
    resp2 = ModelResponse(
        content="Config file created successfully.",
        thinking="File write verified.",
    )
    router.set_mock_responses([resp1, resp2])

    coordinator = AgentCoordinator(workspace=workspace, router=router)
    result = await coordinator.execute_turn("Please create config.json")

    assert "Config file created successfully." in result
    assert (workspace / "config.json").exists()
    assert '{"version": "1.0", "name": "test"}' in (workspace / "config.json").read_text()

    # Verify transcripts were generated
    assert coordinator.transcripts.compact_file.exists()
    assert coordinator.transcripts.full_file.exists()
    assert coordinator.transcripts.step_counter >= 3  # USER, MODEL, TOOL, MODEL

    # Verify memory index
    mem_results = coordinator.memory.search("config")
    assert len(mem_results) > 0


@pytest.mark.asyncio
async def test_coordinator_view_skill_and_command(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    router = ModelRouter()
    resp1 = ModelResponse(
        content="Checking the code-architect skill playbook.",
        tool_calls=[
            ToolCall(
                id="call_skill",
                name="view_skill",
                arguments={"SkillName": "code-architect"},
            )
        ],
    )
    resp2 = ModelResponse(
        content="Skill reviewed. Now running safe echo test.",
        tool_calls=[
            ToolCall(
                id="call_cmd",
                name="run_command",
                arguments={"CommandLine": "echo 'Harness Execution Green'"},
            )
        ],
    )
    resp3 = ModelResponse(content="All steps executed and verified.")
    router.set_mock_responses([resp1, resp2, resp3])

    coordinator = AgentCoordinator(workspace=workspace, router=router)
    result = await coordinator.execute_turn("Review architect skill and test echo")

    assert "All steps executed and verified." in result

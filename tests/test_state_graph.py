import pytest
from harness.orchestration.state_graph import AgentState, StateGraphEngine


@pytest.mark.asyncio
async def test_state_graph_execution_and_rollback():
    engine = StateGraphEngine()

    def triage_node(state: AgentState) -> AgentState:
        state.messages.append({"role": "system", "content": "Triaged task"})
        return state

    def coder_node(state: AgentState) -> AgentState:
        state.messages.append({"role": "assistant", "content": "Wrote code patch"})
        state.test_results = {"passes": state.iteration_count >= 2}
        return state

    def router_fn(state: AgentState) -> str:
        if state.current_agent == "triage":
            return "coder"
        elif state.current_agent == "coder":
            if state.test_results.get("passes"):
                return "__END__"
            return "coder"  # loop
        return "__END__"

    engine.add_node("triage", triage_node)
    engine.add_node("coder", coder_node)
    engine.add_conditional_edge("triage", router_fn)
    engine.add_conditional_edge("coder", router_fn)

    initial_state = AgentState(current_agent="triage")
    final_state = await engine.execute(initial_state, max_steps=5)

    assert final_state.is_complete is True
    assert final_state.test_results["passes"] is True
    assert len(final_state.messages) >= 2

    # Test Time-Travel Rollback: retrieve step 1 checkpoint
    step1_state = engine.rollback_to_step(1)
    assert step1_state is not None
    assert step1_state.current_agent == "triage"
    assert len(step1_state.messages) == 1
    assert step1_state.is_complete is False

    engine.close()

from harness.models.prefix_cache import PromptCacheBuilder, PromptTiers


def test_prefix_cache_invariance():
    tiers = PromptTiers(
        static_system_identity="You are Harness-Zero.",
        tool_schemas=[
            {"name": "write_file", "description": "Writes file"},
            {"name": "edit_file", "description": "Edits file"},
            {"name": "browse", "description": "Web browsing"},
        ],
        skills_catalog="## Available Skills:\n- test-skill",
        conversation_history=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ],
        current_user_prompt="Run pytest",
    )

    messages = PromptCacheBuilder.build_cached_prompt(tiers)
    assert len(messages) == 4  # system + 2 history turns + current user turn
    assert messages[0]["role"] == "system"

    # Verify alphabetical sort of tools in system prompt
    sys_content = messages[0]["content"]
    browse_idx = sys_content.find("browse")
    edit_idx = sys_content.find("edit_file")
    write_idx = sys_content.find("write_file")

    assert browse_idx < edit_idx < write_idx

    # Verify dynamic turn tail contains USER_REQUEST and CONTEXT_TIMESTAMP
    tail_content = messages[-1]["content"]
    assert "<USER_REQUEST>" in tail_content
    assert "Run pytest" in tail_content
    assert "<CONTEXT_TIMESTAMP>" in tail_content

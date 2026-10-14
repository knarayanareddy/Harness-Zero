from harness.models.reasoning_stream import QwenToolAdapter, ReasoningStreamParser, ZAIToolAdapter


def test_deepseek_r1_reasoning_stream_chunking():
    parser = ReasoningStreamParser()

    # Stream chunks split across tag boundaries
    raw_chunks = [
        "Hello, let me think ",
        "<th",
        "ink>First calculate ",
        "2 + 2 = 4.",
        "</th",
        "ink>The answer is 4."
    ]

    emitted = []
    for c in raw_chunks:
        emitted.extend(parser.process_chunk(c))
    emitted.extend(parser.flush())

    thought_text = "".join(chunk.content for chunk in emitted if chunk.is_thought)
    answer_text = "".join(chunk.content for chunk in emitted if not chunk.is_thought)

    assert "First calculate 2 + 2 = 4." in thought_text
    assert "Hello, let me think " in answer_text
    assert "The answer is 4." in answer_text

def test_deepseek_full_text_parser():
    full = "<think>Pondering life\nand universe</think>42 is the answer."
    thought, answer = ReasoningStreamParser.parse_full_text(full)
    assert thought == "Pondering life\nand universe"
    assert answer == "42 is the answer."

def test_qwen_agent_tool_adapter():
    tools = [
        {"name": "fetch_url", "description": "Fetch webpage", "parameters": {"type": "object"}}
    ]
    prompt = QwenToolAdapter.encode_tools(tools)
    assert "fetch_url" in prompt
    assert "<tool_call>" in prompt

    call_sample = """
    I will fetch the page now.
    <tool_call>
    {"name": "fetch_url", "arguments": {"url": "https://example.com"}}
    </tool_call>
    """
    parsed = QwenToolAdapter.parse_tool_calls(call_sample)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "fetch_url"
    assert parsed[0]["arguments"]["url"] == "https://example.com"

def test_zai_tool_adapter():
    sample = """
    ```tool_call
    {"name": "read_file", "arguments": {"path": "/app/main.py"}}
    ```
    """
    calls = ZAIToolAdapter.parse_zcode_calls(sample)
    assert len(calls) == 1
    assert calls[0]["name"] == "read_file"
    assert calls[0]["arguments"]["path"] == "/app/main.py"

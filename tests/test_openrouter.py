import pytest
from unittest.mock import AsyncMock, patch
from harness.models.router import ModelRouter
from harness.core.types import ModelResponse, ToolCall

@pytest.mark.asyncio
async def test_openrouter_routing_and_payload():
    router = ModelRouter(api_keys={"openrouter": "sk-or-v1-mock-test-key"})
    
    mock_api_response = {
        "id": "gen-12345",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Result is 42",
                    "reasoning": "Step 1: Compute answer. Step 2: Return 42.",
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "type": "function",
                            "function": {
                                "name": "run_command",
                                "arguments": '{"CommandLine": "echo 42"}'
                            }
                        }
                    ]
                }
            }
        ]
    }

    from unittest.mock import MagicMock
    mock_client = AsyncMock()
    mock_post_resp = MagicMock()
    mock_post_resp.json.return_value = mock_api_response
    mock_post_resp.raise_for_status = MagicMock()
    mock_client.post.return_value = mock_post_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = await router.generate(
            messages=[{"role": "user", "content": "Compute 42"}],
            model="liquid/lfm-2.5-2.6b:free",
        )

        assert resp.content == "Result is 42"
        assert "Step 1" in resp.thinking
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "run_command"
        assert resp.tool_calls[0].arguments == {"CommandLine": "echo 42"}

        # Check headers passed to OpenRouter
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-or-v1-mock-test-key"
        assert kwargs["headers"]["HTTP-Referer"] == "https://github.com/knarayanareddy/Harness-Zero"
        assert kwargs["json"]["model"] == "liquid/lfm-2.5-2.6b:free"

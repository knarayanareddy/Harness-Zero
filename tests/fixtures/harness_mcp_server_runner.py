"""
Test runner spawning MCPServer over stdio for round-trip integration testing (§10, BRG-03).
"""
import asyncio
import sys

from harness.plugins.bridges.mcp_server import MCPServer, run_mcp_server_from_stdio
from harness.sdk.contracts import ToolCall, ToolSpec
from harness.sdk.types import RiskClass, TextBlock, ToolResult


def sample_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="math_add",
            description="Adds two integers",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
            risk_class=RiskClass.READ,
        ),
        ToolSpec(
            name="secret_admin_tool",
            description="Admin only tool",
            input_schema={"type": "object", "properties": {}},
            risk_class=RiskClass.DESTRUCTIVE,
        ),
    ]


async def execute_sample_tool(call: ToolCall) -> ToolResult:
    if call.name == "math_add":
        a = call.arguments.get("a", 0)
        b = call.arguments.get("b", 0)
        return ToolResult(content=[TextBlock(text=f"Sum: {a + b}")])
    elif call.name == "secret_admin_tool":
        return ToolResult(content=[TextBlock(text="Admin executed")])
    return ToolResult.error(f"Unknown tool: {call.name}")


async def main():
    # If "--filter-math" in argv, only expose math_add
    expose_list = ["math_add"] if "--filter-math" in sys.argv else None

    server = MCPServer(
        tools_provider=sample_tools,
        tool_executor=execute_sample_tool,
        expose_list=expose_list,
    )
    await run_mcp_server_from_stdio(server)


if __name__ == "__main__":
    asyncio.run(main())

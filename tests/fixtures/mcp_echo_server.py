"""
Minimal stdio JSON-RPC 2.0 MCP Echo Server fixture for testing MCP bridges (§10, BRG-02, SIM-01).
"""
import json
import sys


def handle_request(req: dict) -> dict | None:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "resources": {},
                    "prompts": {},
                },
                "serverInfo": {
                    "name": "mcp-echo-server",
                    "version": "1.0.0",
                },
            },
        }

    if method == "notifications/initialized":
        # Notification: no response
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echoes back the input message",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string", "description": "Message to echo"},
                            },
                            "required": ["message"],
                        },
                        "annotations": {"readOnly": True},
                    },
                    {
                        "name": "mutate_data",
                        "description": "Mutates data with destructive tag",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "target": {"type": "string"},
                            },
                            "required": ["target"],
                        },
                        "annotations": {"destructive": True},
                    },
                    {
                        "name": "fail_tool",
                        "description": "Tool that returns an MCP error result",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            },
        }

    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "echo":
            msg = args.get("message", "")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"echo: {msg}"}],
                    "isError": False,
                },
            }
        elif tool_name == "mutate_data":
            target = args.get("target", "")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"mutated {target}"}],
                    "isError": False,
                },
            }
        elif tool_name == "fail_tool":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": "Operation failed in tool"}],
                    "isError": True,
                },
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method {tool_name} not found"},
            }

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "resources": [
                    {
                        "uri": "memo://today",
                        "name": "Today's Memo",
                        "mimeType": "text/plain",
                    }
                ]
            },
        }

    if method == "resources/read":
        params = req.get("params", {})
        uri = params.get("uri", "")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": f"Content of {uri}",
                    }
                ]
            },
        }

    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "prompts": [
                    {
                        "name": "summarize",
                        "description": "Summarize content",
                    }
                ]
            },
        }

    if method == "prompts/get":
        params = req.get("params", {})
        prompt_name = params.get("name")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "description": f"Prompt for {prompt_name}",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": f"Please {prompt_name} this."},
                    }
                ],
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method {method} not found"},
    }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            res = handle_request(req)
            if res is not None:
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()
        except Exception as err:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {err}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

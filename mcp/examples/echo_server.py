#!/usr/bin/env python3
"""Minimal MCP stdio echo server for testing the MCP bridge.

This server implements the Model Context Protocol over stdin/stdout:
  1. Handles `initialize` handshake
  2. Lists one echo tool
  3. Echoes back arguments

Usage:
  python mcp/examples/echo_server.py

Then in another terminal, start the daemon — it will auto-connect because
the server is listed in ~/.claude/mcp_servers.json.
"""

import sys
import json


def respond(msg: dict):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        try:
            req = json.loads(line.strip())
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        req_id = req.get("id")

        if method == "initialize":
            respond({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echo-server", "version": "1.0"},
                }
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            respond({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo_tool",
                            "description": "Echo back the input for testing",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "message": {
                                        "type": "string",
                                        "description": "The message to echo",
                                    },
                                    "repeat": {
                                        "type": "integer",
                                        "description": "Number of times to repeat",
                                    },
                                },
                                "required": ["message"],
                            },
                        },
                        {
                            "name": "calculate",
                            "description": "Simple calculator: add two numbers",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "integer", "description": "First number"},
                                    "b": {"type": "integer", "description": "Second number"},
                                },
                                "required": ["a", "b"],
                            },
                        },
                    ]
                }
            })
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name", "")
            args = params.get("arguments", {})

            if name == "echo_tool":
                msg = args.get("message", "")
                repeat = args.get("repeat", 1)
                echoed = "\n".join([msg] * repeat)
                respond({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": f"Echo: {echoed}"}
                        ],
                    }
                })
            elif name == "calculate":
                a = args.get("a", 0)
                b = args.get("b", 0)
                total = a + b
                respond({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": f"{a} + {b} = {total}"}
                        ],
                    }
                })
            else:
                respond({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                        "isError": True,
                    }
                })
        else:
            # Unknown method — respond with error
            respond({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


if __name__ == "__main__":
    main()

"""Lightweight MCP stdio client.

Connects to an MCP server process via stdin/stdout, sends JSON-RPC 2.0
requests, and receives responses.  Designed for the Model Context Protocol
where each message is a single JSON line delimited by ``\\n``.

Usage::

    client = McpStdioClient(cmd=["node", "server.js"])
    await client.start()
    tools = await client.list_tools()
    result = await client.call_tool("tool_name", {"arg": "val"})
    await client.stop()
"""

import json
import os
import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpToolDef:
    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)


class McpStdioClient:
    """MCP client communicating over subprocess stdin/stdout."""

    def __init__(self, name: str, cmd: list[str], cwd: str | None = None,
                 env: dict | None = None):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._req_id = 0
        self._read_task: asyncio.Task | None = None
        self._initialized = False

    async def start(self, timeout: float = 10.0):
        self._proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env={**(self.env or {}), **{k: v for k, v in
                 (os.environ if not self.env else {}).items()}},
        )
        self._writer = self._proc.stdin
        self._reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(self._reader)
        loop = asyncio.get_event_loop()
        await loop.connect_read_pipe(
            lambda: protocol, self._proc.stdout,
        )

        # background reader
        self._read_task = asyncio.create_task(self._read_loop())

        # initialize handshake
        init_result = await self._request(
            "initialize",
            {"protocolVersion": "2024-11-05",
             "capabilities": {},
             "clientInfo": {"name": "ai-draw", "version": "1.0"}},
            timeout=timeout,
        )
        # send initialized notification
        await self._send_notification("notifications/initialized", {})
        self._initialized = True

    async def stop(self):
        if self._read_task:
            self._read_task.cancel()
            self._read_task = None
        if self._proc:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
            self._proc = None

    async def list_tools(self) -> list[McpToolDef]:
        result = await self._request("tools/list", {})
        raw_tools = result.get("tools", [])
        tools = []
        for t in raw_tools:
            tools.append(McpToolDef(
                name=t.get("name", ""),
                description=t.get("description", ""),
                parameters=t.get("inputSchema", t.get("parameters", {})),
            ))
        return tools

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        params = {"name": name}
        if arguments:
            params["arguments"] = arguments
        return await self._request("tools/call", params)

    # ── internal ─────────────────────────────────────────────────

    async def _request(self, method: str, params: dict,
                       timeout: float = 60.0) -> dict:
        self._req_id += 1
        req_id = self._req_id
        future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }, ensure_ascii=False)
        self._writer.write((msg + "\n").encode())
        await self._writer.drain()

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise

    async def _send_notification(self, method: str, params: dict):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }, ensure_ascii=False)
        self._writer.write((msg + "\n").encode())
        await self._writer.drain()

    async def _read_loop(self):
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue

                # JSON-RPC response
                if "id" in msg and msg.get("id") is not None:
                    req_id = msg["id"]
                    future = self._pending.pop(req_id, None)
                    if future is not None and not future.done():
                        if "error" in msg:
                            future.set_exception(
                                Exception(msg["error"].get("message", "MCP error"))
                            )
                        else:
                            future.set_result(msg.get("result", {}))
                # Notification — ignore
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

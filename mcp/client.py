"""Lightweight synchronous MCP stdio client.

Connects to an MCP server process via stdin/stdout using plain
``subprocess.Popen`` and synchronous I/O.  Designed for ``McpToolWrapper``
which implements ``BaseTool.execute()`` (synchronous) — no async bridging
is needed.
"""

import json
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any
from queue import Queue, Empty


@dataclass
class McpToolDef:
    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)


class McpSyncClient:
    """MCP client using synchronous subprocess stdio."""

    def __init__(self, name: str, cmd: list[str], cwd: str | None = None,
                 env: dict | None = None):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._req_id = 0
        self._initialized = False

    def start(self, timeout: float = 10.0):
        merged_env = dict(subprocess.os.environ)
        if self.env:
            merged_env.update(self.env)

        self._proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=merged_env,
        )

        # initialize handshake — _send_request uses _communicate which
        # handles responses (no select.select needed, works on Windows)
        self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ai-draw", "version": "1.0"},
        })

        # send initialized notification (no response expected)
        self._send_notification("notifications/initialized", {})
        self._initialized = True

    def stop(self):
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                if self._proc:
                    self._proc.kill()
            self._proc = None

    def list_tools(self) -> list[McpToolDef]:
        result = self._send_request("tools/list", {})
        raw_tools = result.get("tools", [])
        tools = []
        for t in raw_tools:
            tools.append(McpToolDef(
                name=t.get("name", ""),
                description=t.get("description", ""),
                parameters=t.get("inputSchema", t.get("parameters", {})),
            ))
        return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        params = {"name": name}
        if arguments:
            params["arguments"] = arguments
        return self._send_request("tools/call", params)

    # ── internal ─────────────────────────────────────────────────

    def _send_request(self, method: str, params: dict) -> dict:
        self._req_id += 1
        msg = json.dumps({
            "jsonrpc": "2.0", "id": self._req_id,
            "method": method, "params": params,
        }, ensure_ascii=False)
        return self._communicate(msg)

    def _send_notification(self, method: str, params: dict):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": method, "params": params,
        }, ensure_ascii=False)
        with self._lock:
            self._proc.stdin.write((msg + "\n").encode())
            self._proc.stdin.flush()

    def _communicate(self, msg: str) -> dict:
        with self._lock:
            self._proc.stdin.write((msg + "\n").encode())
            self._proc.stdin.flush()

            # read until we get a jsonrpc response matching our id
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    raise ConnectionError("MCP server closed connection")
                try:
                    resp = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == self._req_id:
                    if "error" in resp:
                        raise RuntimeError(
                            resp["error"].get("message", "MCP error")
                        )
                    return resp.get("result", {})


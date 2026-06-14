"""MCP server lifecycle manager.

At daemon startup the manager reads ``mcp/config.json``, spawns each MCP
server process, lists its tools, wraps them as ``BaseTool`` instances via
``McpToolWrapper``, and makes them available to the ``ToolRegistry``.
"""

import os
import json
from .client import McpStdioClient
from .bridge import McpToolWrapper, _mcp_schema_to_params

MCP_CONFIG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "config.json")
)

# Global MCP config in the user's home directory, merged with the project config.
GLOBAL_MCP_CONFIG = os.path.normpath(
    os.path.join(os.path.expanduser("~"), ".claude", "mcp_servers.json")
)


class McpManager:
    """Manages multiple MCP server connections and their tool wrappers.

    Usage::

        mgr = McpManager()
        await mgr.start()
        # mgr.tools is a list of McpToolWrapper instances
        for tool in mgr.tools:
            registry.register(tool)
        # ...
        await mgr.stop()
    """

    def __init__(self, config_path: str = ""):
        self._config_path = config_path or MCP_CONFIG_PATH
        self._clients: list[McpStdioClient] = []
        self.tools: list[McpToolWrapper] = []

    async def start(self):
        """Read config, spawn servers, collect tools."""
        servers = self._load_config()
        for server_cfg in servers:
            name = server_cfg.get("name", "mcp")
            cmd = server_cfg.get("command", [])
            if not cmd:
                continue
            cwd = server_cfg.get("cwd")
            env = server_cfg.get("env")
            client = McpStdioClient(
                name=name, cmd=cmd, cwd=cwd, env=env,
            )
            try:
                await client.start(timeout=server_cfg.get("timeout", 10.0))
            except Exception as e:
                print(f"[mcp] Failed to start '{name}': {e}")
                continue

            # list tools
            try:
                mcp_tools = await client.list_tools()
            except Exception as e:
                print(f"[mcp] Failed to list tools for '{name}': {e}")
                await client.stop()
                continue

            # wrap each tool
            for mt in mcp_tools:
                params = _mcp_schema_to_params(mt.parameters)
                wrapper = McpToolWrapper(
                    mcp_tool_name=mt.name,
                    description=mt.description,
                    parameters=params,
                    client=client,
                )
                self.tools.append(wrapper)

            self._clients.append(client)
            print(f"[mcp] Connected '{name}' — {len(mcp_tools)} tool(s)")

    async def stop(self):
        for client in self._clients:
            try:
                await client.stop()
            except Exception:
                pass
        self._clients.clear()
        self.tools.clear()

    def _load_config(self) -> list[dict]:
        """Load servers from project config and global config, merged."""
        servers = []
        seen_names = set()

        for path in (self._config_path, GLOBAL_MCP_CONFIG):
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                for server in data.get("servers", []):
                    name = server.get("name", "")
                    if name and name not in seen_names:
                        servers.append(server)
                        seen_names.add(name)
            except Exception:
                continue

        return servers

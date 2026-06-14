"""Wrap MCP tool definitions as ``BaseTool`` instances for the registry."""

import json
import asyncio
from tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from .client import McpStdioClient


def _mcp_schema_to_params(input_schema: dict) -> list[ToolParameter]:
    """Convert an MCP JSON Schema input schema to ``ToolParameter`` list."""
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    params = []
    for name, prop in properties.items():
        param_type = prop.get("type", "string")
        # MCP uses "number" for floats; map to our convention
        if param_type == "number":
            param_type = "number"
        elif param_type == "integer":
            param_type = "integer"
        elif param_type == "boolean":
            param_type = "boolean"
        elif param_type == "array":
            param_type = "string"
        elif param_type == "object":
            param_type = "string"
        else:
            param_type = "string"

        params.append(ToolParameter(
            name=name,
            type=param_type,
            description=prop.get("description", ""),
            required=name in required,
        ))
    return params


class McpToolWrapper(BaseTool):
    """Wrap an MCP tool as a ``BaseTool`` so it can be registered in the
    local ``ToolRegistry`` and called through the normal agent flow."""

    def __init__(self, mcp_tool_name: str, description: str,
                 parameters: list[ToolParameter],
                 client: McpStdioClient):
        self._mcp_name = mcp_tool_name
        self._desc = description
        self._params = parameters
        self._client = client
        self._defn = ToolDefinition(
            name=mcp_tool_name,
            description=description,
            parameters=parameters,
        )

    def definition(self) -> ToolDefinition:
        return self._defn

    def execute(self, **kwargs) -> ToolResult:
        """Execute the MCP tool via the client.

        The underlying MCP call is async, so we bridge via
        ``asyncio.run()`` or a new event loop when one is already running.
        """
        try:
            result = asyncio.run(self._async_call(**kwargs))
            return result
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(self._async_call(**kwargs))
                return result
            finally:
                loop.close()

    async def _async_call(self, **kwargs) -> ToolResult:
        try:
            mcp_result = await self._client.call_tool(self._mcp_name, kwargs)
        except Exception as e:
            return ToolResult(
                is_error=True,
                error=f"MCP tool '{self._mcp_name}' failed: {e}",
                error_type="execution_error",
            )

        # MCP tool results can have multiple content items
        content_parts = []
        is_error = False
        for item in mcp_result.get("content", []):
            if item.get("type") == "text":
                content_parts.append(item.get("text", ""))
            elif item.get("type") == "resource":
                resource = item.get("resource", {})
                blob = resource.get("blob", "")
                text = resource.get("text", "")
                content_parts.append(text or f"[resource: {resource.get('uri', '')}]")
            elif item.get("type") == "image":
                content_parts.append(f"[image: {item.get('mimeType', '')}]")

        if mcp_result.get("isError"):
            is_error = True

        content = "\n".join(content_parts)

        if is_error:
            return ToolResult(
                is_error=True,
                error=content or f"MCP tool '{self._mcp_name}' returned an error",
                error_type="execution_error",
                data=mcp_result,
            )

        return ToolResult(
            is_error=False,
            data=mcp_result,
            description=content[:500] if content else f"MCP tool '{self._mcp_name}' completed",
        )

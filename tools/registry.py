from typing import Any
from pydantic import ValidationError
from .base import BaseTool, ToolResult


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._permissions = None

    @property
    def permissions(self):
        if self._permissions is None:
            from .manager import PermissionManager
            self._permissions = PermissionManager()
        return self._permissions

    @permissions.setter
    def permissions(self, value):
        self._permissions = value

    def register(self, tool: BaseTool) -> "ToolRegistry":
        defn = tool.definition()
        self._tools[defn.name] = tool
        return self

    def register_all(self, tools: list[BaseTool]) -> "ToolRegistry":
        for tool in tools:
            self.register(tool)
        return self

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_tool_definitions(self) -> list[dict]:
        return [tool.definition().to_openai() for tool in self._tools.values()]

    def execute(self, name: str, **kwargs) -> ToolResult:
        """Validate params and execute the tool.  No permission check here."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(is_error=True, error=f"Unknown tool: {name}",
                              error_type="not_found")

        # 1. Pydantic parameter validation
        try:
            validated = tool.validate_params(kwargs)
        except ValidationError as e:
            return ToolResult(
                is_error=True,
                error=f"Invalid arguments for '{name}': {e}",
                error_type="invalid_args",
            )

        # 2. Execute
        try:
            result = tool.execute(**validated)
            if result.is_error and not result.error_type:
                result.error_type = "execution_error"
            return result
        except Exception as e:
            return ToolResult(is_error=True,
                              error=f"Tool execution failed: {e}",
                              error_type="exception")

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

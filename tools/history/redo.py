from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class RedoTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="redo",
            description="Redo the last undone operation",
            parameters=[],
        )

    def execute(self, **kwargs) -> ToolResult:
        code = (
            "if (typeof redoStack !== 'undefined' && redoStack.length > 0) {"
            "const state = redoStack.pop();"
            "canvas.loadFromJSON(state, () => canvas.renderAll());"
            "}"
        )
        description = "Redid last undone operation"
        return ToolResult(code=code, description=description)

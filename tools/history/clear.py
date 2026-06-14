from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class ClearCanvasTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="clear_canvas",
            description="Clear all objects from the canvas",
            parameters=[],
        )

    def execute(self, **kwargs) -> ToolResult:
        code = "canvas.clear(); canvas.backgroundColor = '#ffffff'; canvas.renderAll();"
        description = "Cleared the canvas"
        return ToolResult(code=code, description=description)

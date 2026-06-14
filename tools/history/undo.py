from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class UndoTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="undo",
            description="Undo the last drawing operation",
            parameters=[],
        )

    def execute(self, **kwargs) -> ToolResult:
        code = (
            "if (typeof undoStack !== 'undefined' && undoStack.length > 0) {"
            "const state = undoStack.pop();"
            "canvas.loadFromJSON(state, () => canvas.renderAll());"
            "} else {"
            "const objs = canvas.getObjects();"
            "if (objs.length > 0) { canvas.remove(objs.at(-1)); canvas.renderAll(); }"
            "}"
        )
        description = "Undid last operation"
        return ToolResult(code=code, description=description)

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class MoveObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="move_object",
            description="Move an object on the canvas",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last' (last added), 'all', or index number", required=True),
                ToolParameter(name="x", type="number", description="New X position (default: 0, relative offset)", required=False),
                ToolParameter(name="y", type="number", description="New Y position (default: 0, relative offset)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        selector = kwargs.get("selector", "last")
        dx = kwargs.get("x", 0)
        dy = kwargs.get("y", 0)

        if selector == "last":
            code = (
                f"const last = canvas.getObjects().at(-1);"
                f"if (last) {{ last.set({{left: last.left + {dx}, top: last.top + {dy}}}); canvas.renderAll(); }}"
            )
            description = f"Moved last object by ({dx}, {dy})"
        elif selector == "all":
            code = (
                f"canvas.getObjects().forEach(obj => {{"
                f"obj.set({{left: obj.left + {dx}, top: obj.top + {dy}}});"
                f"}}); canvas.renderAll();"
            )
            description = f"Moved all objects by ({dx}, {dy})"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            code = (
                f"const obj = canvas.getObjects()[{idx}];"
                f"if (obj) {{ obj.set({{left: obj.left + {dx}, top: obj.top + {dy}}}); canvas.renderAll(); }}"
            )
            description = f"Moved object at index {idx} by ({dx}, {dy})"

        return ToolResult(code=code, description=description, data={"selector": selector, "dx": dx, "dy": dy})

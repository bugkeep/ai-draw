from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class MoveObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="move_object",
            description="Move an object on the canvas",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation (e.g. 'circle_1', 'smiley_1')", required=False),
                ToolParameter(name="x", type="number", description="New X position (default: 0, relative offset)", required=False),
                ToolParameter(name="y", type="number", description="New Y position (default: 0, relative offset)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        dx = kwargs.get("x", 0)
        dy = kwargs.get("y", 0)

        if object_id:
            code = (
                f"const obj = canvas.getObjects().find(o => o.objectId === '{object_id}');"
                f"if (obj) {{ obj.set({{left: (obj.left || 0) + {dx}, top: (obj.top || 0) + {dy}}}); canvas.renderAll(); }}"
            )
            description = f"Moved object '{object_id}' by ({dx}, {dy})"
            return ToolResult(code=code, description=description, data={"object_id": object_id, "dx": dx, "dy": dy})

        if selector == "last":
            code = (
                f"const last = canvas.getObjects().at(-1);"
                f"if (last) {{ last.set({{left: (last.left || 0) + {dx}, top: (last.top || 0) + {dy}}}); canvas.renderAll(); }}"
            )
            description = f"Moved last object by ({dx}, {dy})"
        elif selector == "all":
            code = (
                f"canvas.getObjects().forEach(obj => {{"
                f"obj.set({{left: (obj.left || 0) + {dx}, top: (obj.top || 0) + {dy}}});"
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
                f"if (obj) {{ obj.set({{left: (obj.left || 0) + {dx}, top: (obj.top || 0) + {dy}}}); canvas.renderAll(); }}"
            )
            description = f"Moved object at index {idx} by ({dx}, {dy})"

        return ToolResult(code=code, description=description, data={"selector": selector, "dx": dx, "dy": dy})

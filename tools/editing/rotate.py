from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class RotateObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="rotate_object",
            description="Rotate an object on the canvas by degrees",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="degrees", type="number", description="Degrees to rotate by, positive is clockwise", required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        degrees = float(kwargs.get("degrees", 0))

        def rotate_expr(expr: str) -> str:
            return (
                f"const obj = {expr};"
                f"if (obj) {{ obj.rotate(((obj.angle || 0) + {degrees}) % 360); "
                f"obj.setCoords(); canvas.renderAll(); }}"
            )

        if object_id:
            code = rotate_expr(f"canvas.getObjects().find(o => o.objectId === '{object_id}')")
            description = f"Rotated object '{object_id}' by {degrees:g} degrees"
            return ToolResult(code=code, description=description, data={"object_id": object_id, "degrees": degrees})

        if selector == "last":
            code = rotate_expr("canvas.getObjects().at(-1)")
            description = f"Rotated last object by {degrees:g} degrees"
        elif selector == "all":
            code = (
                f"canvas.getObjects().forEach(obj => {{"
                f"obj.rotate(((obj.angle || 0) + {degrees}) % 360);"
                f"obj.setCoords();"
                f"}}); canvas.renderAll();"
            )
            description = f"Rotated all objects by {degrees:g} degrees"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            code = rotate_expr(f"canvas.getObjects()[{idx}]")
            description = f"Rotated object at index {idx} by {degrees:g} degrees"

        return ToolResult(code=code, description=description, data={"selector": selector, "degrees": degrees})

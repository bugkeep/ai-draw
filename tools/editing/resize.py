from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class ResizeObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="resize_object",
            description="Resize an object on the canvas",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation (e.g. 'circle_1', 'smiley_1')", required=False),
                ToolParameter(name="scale_x", type="number", description="Horizontal scale factor (default: 1.0)", required=False),
                ToolParameter(name="scale_y", type="number", description="Vertical scale factor (default: 1.0)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        sx = kwargs.get("scale_x", 1.0)
        sy = kwargs.get("scale_y", 1.0)

        if object_id:
            code = (
                f"const obj = canvas.getObjects().find(o => o.objectId === '{object_id}');"
                f"if (obj) {{ obj.set({{scaleX: (obj.scaleX || 1) * {sx}, scaleY: (obj.scaleY || 1) * {sy}}}); canvas.renderAll(); }}"
            )
            description = f"Rescaled object '{object_id}' by ({sx}, {sy})"
            return ToolResult(code=code, description=description, data={"object_id": object_id, "scale_x": sx, "scale_y": sy})

        if selector == "last":
            code = (
                f"const last = canvas.getObjects().at(-1);"
                f"if (last) {{ last.set({{scaleX: (last.scaleX || 1) * {sx}, scaleY: (last.scaleY || 1) * {sy}}}); canvas.renderAll(); }}"
            )
            description = f"Rescaled last object by ({sx}, {sy})"
        elif selector == "all":
            code = (
                f"canvas.getObjects().forEach(obj => {{"
                f"obj.set({{scaleX: (obj.scaleX || 1) * {sx}, scaleY: (obj.scaleY || 1) * {sy}}});"
                f"}}); canvas.renderAll();"
            )
            description = f"Rescaled all objects by ({sx}, {sy})"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            code = (
                f"const obj = canvas.getObjects()[{idx}];"
                f"if (obj) {{ obj.set({{scaleX: (obj.scaleX || 1) * {sx}, scaleY: (obj.scaleY || 1) * {sy}}}); canvas.renderAll(); }}"
            )
            description = f"Rescaled object at index {idx} by ({sx}, {sy})"

        return ToolResult(code=code, description=description, data={"selector": selector, "scale_x": sx, "scale_y": sy})

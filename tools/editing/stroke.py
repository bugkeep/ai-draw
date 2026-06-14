from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class ChangeStrokeTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="change_stroke",
            description="Change object outline stroke color and width",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="stroke", type="string", description="Outline color", required=False),
                ToolParameter(name="stroke_width", type="number", description="Outline width in pixels", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        stroke = kwargs.get("stroke", "#1F2937")
        stroke_width = float(kwargs.get("stroke_width", 2))
        if stroke_width < 0 or stroke_width > 80:
            return ToolResult(is_error=True, error="stroke_width must be between 0 and 80")

        update = f"{{ stroke: '{stroke}', strokeWidth: {stroke_width} }}"
        if object_id:
            code = (
                f"const obj = canvas.getObjects().find(o => o.objectId === '{object_id}');"
                f"if (obj) {{ obj.set({update}); obj.setCoords(); canvas.renderAll(); }}"
            )
            return ToolResult(
                code=code,
                description=f"Changed object '{object_id}' stroke to {stroke} width {stroke_width:g}",
                data={"object_id": object_id, "stroke": stroke, "stroke_width": stroke_width},
            )

        if selector == "all":
            code = (
                f"canvas.getObjects().forEach(obj => {{ obj.set({update}); obj.setCoords(); }});"
                f"canvas.renderAll();"
            )
            target = "all objects"
        elif selector == "last":
            code = (
                f"const obj = canvas.getObjects().at(-1);"
                f"if (obj) {{ obj.set({update}); obj.setCoords(); canvas.renderAll(); }}"
            )
            target = "last object"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            code = (
                f"const obj = canvas.getObjects()[{idx}];"
                f"if (obj) {{ obj.set({update}); obj.setCoords(); canvas.renderAll(); }}"
            )
            target = f"object at index {idx}"

        return ToolResult(
            code=code,
            description=f"Changed {target} stroke to {stroke} width {stroke_width:g}",
            data={"selector": selector, "stroke": stroke, "stroke_width": stroke_width},
        )

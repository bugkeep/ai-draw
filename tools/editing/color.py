from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class ChangeColorTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="change_color",
            description="Change the color of object(s) on the canvas",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation (e.g. 'circle_1', 'smiley_1')", required=False),
                ToolParameter(name="color", type="string", description="New color (hex or name)", required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        color = kwargs.get("color", "#000000")

        if object_id:
            code = (
                f"const obj = canvas.getObjects().find(o => o.objectId === '{object_id}');"
                f"if (obj) {{ obj.set({{fill: '{color}'}}); canvas.renderAll(); }}"
            )
            description = f"Changed object '{object_id}' color to {color}"
            return ToolResult(code=code, description=description, data={"object_id": object_id, "color": color})

        if selector == "last":
            code = (
                f"const last = canvas.getObjects().at(-1);"
                f"if (last) {{ last.set({{fill: '{color}'}}); canvas.renderAll(); }}"
            )
            description = f"Changed last object color to {color}"
        elif selector == "all":
            code = (
                f"canvas.getObjects().forEach(obj => {{"
                f"obj.set({{fill: '{color}'}});"
                f"}}); canvas.renderAll();"
            )
            description = f"Changed all objects color to {color}"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            code = (
                f"const obj = canvas.getObjects()[{idx}];"
                f"if (obj) {{ obj.set({{fill: '{color}'}}); canvas.renderAll(); }}"
            )
            description = f"Changed object at index {idx} color to {color}"

        return ToolResult(code=code, description=description, data={"selector": selector, "color": color})

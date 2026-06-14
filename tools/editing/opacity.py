from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class ChangeOpacityTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="change_opacity",
            description="Change object opacity between 0 and 1",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="opacity", type="number", description="Opacity from 0 fully transparent to 1 fully opaque", required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        opacity = float(kwargs.get("opacity", 1))
        if not 0 <= opacity <= 1:
            return ToolResult(is_error=True, error="opacity must be between 0 and 1")

        if object_id:
            code = (
                f"const obj = canvas.getObjects().find(o => o.objectId === '{object_id}');"
                f"if (obj) {{ obj.set({{ opacity: {opacity} }}); obj.setCoords(); canvas.renderAll(); }}"
            )
            return ToolResult(
                code=code,
                description=f"Changed object '{object_id}' opacity to {opacity:g}",
                data={"object_id": object_id, "opacity": opacity},
            )

        if selector == "all":
            code = (
                f"canvas.getObjects().forEach(obj => {{ obj.set({{ opacity: {opacity} }}); obj.setCoords(); }});"
                f"canvas.renderAll();"
            )
            target = "all objects"
        elif selector == "last":
            code = (
                f"const obj = canvas.getObjects().at(-1);"
                f"if (obj) {{ obj.set({{ opacity: {opacity} }}); obj.setCoords(); canvas.renderAll(); }}"
            )
            target = "last object"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            code = (
                f"const obj = canvas.getObjects()[{idx}];"
                f"if (obj) {{ obj.set({{ opacity: {opacity} }}); obj.setCoords(); canvas.renderAll(); }}"
            )
            target = f"object at index {idx}"

        return ToolResult(
            code=code,
            description=f"Changed {target} opacity to {opacity:g}",
            data={"selector": selector, "opacity": opacity},
        )

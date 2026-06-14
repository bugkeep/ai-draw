from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class SelectObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="select_object",
            description="Select an object or multiple objects on the canvas by voice-friendly criteria",
            parameters=[
                ToolParameter(name="object_id", type="string", description="Stable objectId to select", required=False),
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id/type/color is set)", required=False),
                ToolParameter(name="type", type="string", description="Object type to select, e.g. circle, rect, group", required=False),
                ToolParameter(name="color", type="string", description="Fill color to select", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        obj_type = kwargs.get("type", "")
        color = kwargs.get("color", "")

        if object_id:
            expr = f"[canvas.getObjects().find(o => o.objectId === '{object_id}')].filter(Boolean)"
            target = object_id
        elif obj_type or color:
            conditions = []
            if obj_type:
                conditions.append(f"obj.type === '{obj_type}' || obj.semanticType === '{obj_type}'")
            if color:
                conditions.append(f"obj.fill === '{color}' || obj.stroke === '{color}'")
            expr = f"canvas.getObjects().filter(obj => {' && '.join(conditions)})"
            target = "matching objects"
        elif selector == "all":
            expr = "canvas.getObjects()"
            target = "all objects"
        elif selector == "last":
            expr = "[canvas.getObjects().at(-1)].filter(Boolean)"
            target = "last object"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            expr = f"[canvas.getObjects()[{idx}]].filter(Boolean)"
            target = f"object at index {idx}"

        code = (
            f"const selected = {expr};"
            "canvas.discardActiveObject();"
            "if (selected.length === 1) {"
            "  canvas.setActiveObject(selected[0]);"
            "} else if (selected.length > 1) {"
            "  canvas.setActiveObject(new fabric.ActiveSelection(selected, { canvas }));"
            "}"
            "canvas.renderAll();"
        )

        return ToolResult(
            code=code,
            description=f"Selected {target}",
            data={"object_id": object_id, "selector": selector, "type": obj_type, "color": color},
        )

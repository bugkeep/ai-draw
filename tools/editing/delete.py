from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class DeleteObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="delete_object",
            description="Delete object(s) from canvas by object_id, selector, type, or color",
            parameters=[
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation (e.g. 'circle_1', 'smiley_1')", required=False),
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (mutually exclusive with type/color, ignored when object_id is set)", required=False),
                ToolParameter(name="type", type="string", description="Object type to delete (circle, rect, line, text, ellipse)", required=False),
                ToolParameter(name="color", type="string", description="Object color to delete", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "")
        obj_type = kwargs.get("type", "")
        color = kwargs.get("color", "")

        if object_id:
            code = (
                f"const obj = canvas.getObjects().find(o => o.objectId === '{object_id}');"
                f"if (obj) {{ canvas.remove(obj); canvas.renderAll(); }}"
            )
            description = f"Deleted object '{object_id}'"
            return ToolResult(code=code, description=description, data={"object_id": object_id})

        if selector:
            if selector == "last":
                code = (
                    f"const last = canvas.getObjects().at(-1);"
                    f"if (last) {{ canvas.remove(last); canvas.renderAll(); }}"
                )
                description = "Deleted the last object"
            elif selector == "all":
                code = "canvas.clear(); canvas.backgroundColor = '#ffffff'; canvas.renderAll();"
                description = "Deleted all objects"
            else:
                try:
                    idx = int(selector)
                except ValueError:
                    return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
                code = (
                    f"const obj = canvas.getObjects()[{idx}];"
                    f"if (obj) {{ canvas.remove(obj); canvas.renderAll(); }}"
                )
                description = f"Deleted object at index {idx}"
            return ToolResult(code=code, description=description, data={"selector": selector})

        if not obj_type and not color:
            return ToolResult(is_error=True, error="Must specify object_id, selector, type, or color to delete")

        conditions = []
        if obj_type:
            conditions.append(f"obj.type === '{obj_type}'")
        if color:
            conditions.append(f"obj.fill === '{color}'")
        condition = " && ".join(conditions)

        code = (
            f"const objs = canvas.getObjects().filter(obj => {condition});"
            f"objs.forEach(obj => canvas.remove(obj));"
            f"canvas.renderAll();"
        )
        description = f"Deleted objects matching type='{obj_type}' color='{color}'"
        return ToolResult(code=code, description=description, data={"type": obj_type, "color": color})

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class DeleteObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="delete_object",
            description="Delete object(s) from canvas by type or color",
            parameters=[
                ToolParameter(name="type", type="string", description="Object type to delete (circle, rect, line, text, ellipse)", required=False),
                ToolParameter(name="color", type="string", description="Object color to delete", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        obj_type = kwargs.get("type", "")
        color = kwargs.get("color", "")

        if not obj_type and not color:
            return ToolResult(is_error=True, error="Must specify type or color to delete")

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

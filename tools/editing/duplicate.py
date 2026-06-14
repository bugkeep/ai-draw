import json

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class DuplicateObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="duplicate_object",
            description="Duplicate an existing canvas object with an offset",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last' or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="offset_x", type="number", description="Duplicate horizontal offset in pixels (default: 30)", required=False),
                ToolParameter(name="offset_y", type="number", description="Duplicate vertical offset in pixels (default: 30)", required=False),
                ToolParameter(name="new_object_id", type="string", description="Optional objectId for the duplicate", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        offset_x = float(kwargs.get("offset_x", 30))
        offset_y = float(kwargs.get("offset_y", 30))
        new_object_id = kwargs.get("new_object_id", "")

        if object_id:
            expr = f"canvas.getObjects().find(o => o.objectId === {json.dumps(object_id)})"
            target = object_id
        elif selector == "last":
            expr = "canvas.getObjects().at(-1)"
            target = "last object"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            expr = f"canvas.getObjects()[{idx}]"
            target = f"object at index {idx}"

        new_id_expr = json.dumps(new_object_id) if new_object_id else "`${source.objectId || source.type || 'object'}_copy_${Date.now()}`"
        code = (
            "await (async () => {"
            f"const source = {expr};"
            "if (source) {"
            "  const clone = await new Promise(resolve => source.clone(resolve));"
            f"  clone.set({{ left: (source.left || 0) + {offset_x}, top: (source.top || 0) + {offset_y}, "
            f"objectId: {new_id_expr}, semanticType: source.semanticType }});"
            "  clone.setCoords();"
            "  canvas.add(clone);"
            "  canvas.setActiveObject(clone);"
            "  canvas.renderAll();"
            "}"
            "})();"
        )

        return ToolResult(
            code=code,
            description=f"Duplicated {target} by ({offset_x:g}, {offset_y:g})",
            data={
                "object_id": object_id,
                "selector": selector,
                "offset_x": offset_x,
                "offset_y": offset_y,
                "new_object_id": new_object_id,
            },
        )

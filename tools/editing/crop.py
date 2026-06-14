from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class CropObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="crop_object",
            description="Apply a rectangular crop clipPath to an object",
            parameters=[
                ToolParameter(name="object_id", type="string", description="Stable objectId to crop", required=False),
                ToolParameter(name="selector", type="string", description="Object selector: 'last' or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="x", type="number", description="Crop rectangle left position relative to object bounds (default: 0)", required=False),
                ToolParameter(name="y", type="number", description="Crop rectangle top position relative to object bounds (default: 0)", required=False),
                ToolParameter(name="width", type="number", description="Crop rectangle width. Defaults to 80% of object width.", required=False),
                ToolParameter(name="height", type="number", description="Crop rectangle height. Defaults to 80% of object height.", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        x = float(kwargs.get("x", 0))
        y = float(kwargs.get("y", 0))
        width = kwargs.get("width")
        height = kwargs.get("height")
        if width is not None and float(width) <= 0:
            return ToolResult(is_error=True, error="width must be positive")
        if height is not None and float(height) <= 0:
            return ToolResult(is_error=True, error="height must be positive")

        if object_id:
            expr = f"canvas.getObjects().find(o => o.objectId === '{object_id}')"
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

        width_js = "Math.max(1, (obj.width || bounds.width || 1) * 0.8)" if width is None else str(float(width))
        height_js = "Math.max(1, (obj.height || bounds.height || 1) * 0.8)" if height is None else str(float(height))
        code = f"""
const obj = {expr};
if (obj) {{
  const bounds = obj.getBoundingRect(true, true);
  const clip = new fabric.Rect({{
    left: {x},
    top: {y},
    width: {width_js},
    height: {height_js},
    originX: 'left',
    originY: 'top',
    absolutePositioned: false
  }});
  obj.set({{ clipPath: clip }});
  obj.setCoords();
  canvas.setActiveObject(obj);
  canvas.renderAll();
}}
""".strip()

        return ToolResult(
            code=code,
            description=f"Applied rectangular crop to {target}",
            data={"object_id": object_id, "selector": selector, "x": x, "y": y, "width": width, "height": height},
        )

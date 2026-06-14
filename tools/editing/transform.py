import json

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


FLIP_AXES = ["horizontal", "vertical", "both"]


def _json(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _objects_expr(object_id: str, selector: str) -> tuple[str, str] | ToolResult:
    if object_id:
        return f"[canvas.getObjects().find(o => o.objectId === {_json(object_id)})].filter(Boolean)", object_id
    if selector == "all":
        return "canvas.getObjects()", "all objects"
    if selector == "last":
        return "[canvas.getObjects().at(-1)].filter(Boolean)", "last object"
    try:
        idx = int(selector)
    except ValueError:
        return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
    return f"[canvas.getObjects()[{idx}]].filter(Boolean)", f"object at index {idx}"


def _select_objects_js(objects_expr: str) -> str:
    return (
        f"const objects = {objects_expr};"
        "objects.forEach(obj => { obj.setCoords(); });"
        "canvas.discardActiveObject();"
        "if (objects.length === 1) {"
        "  canvas.setActiveObject(objects[0]);"
        "} else if (objects.length > 1) {"
        "  canvas.setActiveObject(new fabric.ActiveSelection(objects, { canvas }));"
        "}"
        "canvas.renderAll();"
    )


class FlipObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="flip_object",
            description="Flip or mirror an object horizontally, vertically, or both",
            parameters=[
                ToolParameter(name="object_id", type="string", description="Stable objectId to flip", required=False),
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="axis", type="string", description="Flip axis", required=False, enum=FLIP_AXES),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "") or ""
        selector = kwargs.get("selector", "last") or "last"
        axis = kwargs.get("axis") or "horizontal"
        if axis not in FLIP_AXES:
            return ToolResult(is_error=True, error=f"Invalid flip axis: {axis}")
        target = _objects_expr(object_id, selector)
        if isinstance(target, ToolResult):
            return target
        expr, label = target
        flip_x = axis in ("horizontal", "both")
        flip_y = axis in ("vertical", "both")
        code = f"""
const objects = {expr};
objects.forEach(obj => {{
  if ({str(flip_x).lower()}) obj.set({{ flipX: !obj.flipX }});
  if ({str(flip_y).lower()}) obj.set({{ flipY: !obj.flipY }});
  obj.setCoords();
}});
canvas.discardActiveObject();
if (objects.length === 1) {{
  canvas.setActiveObject(objects[0]);
}} else if (objects.length > 1) {{
  canvas.setActiveObject(new fabric.ActiveSelection(objects, {{ canvas }}));
}}
canvas.renderAll();
""".strip()
        return ToolResult(
            code=code,
            description=f"Flipped {label} on {axis} axis",
            data={"object_id": object_id, "selector": selector, "axis": axis},
        )


class SkewObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skew_object",
            description="Skew or slant an object by degrees on the X and/or Y axis",
            parameters=[
                ToolParameter(name="object_id", type="string", description="Stable objectId to skew", required=False),
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="skew_x", type="number", description="Horizontal skew angle in degrees (default: 0)", required=False),
                ToolParameter(name="skew_y", type="number", description="Vertical skew angle in degrees (default: 0)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "") or ""
        selector = kwargs.get("selector", "last") or "last"
        skew_x = float(kwargs.get("skew_x", 0) or 0)
        skew_y = float(kwargs.get("skew_y", 0) or 0)
        if not -89 <= skew_x <= 89 or not -89 <= skew_y <= 89:
            return ToolResult(is_error=True, error="skew_x and skew_y must be between -89 and 89 degrees")
        target = _objects_expr(object_id, selector)
        if isinstance(target, ToolResult):
            return target
        expr, label = target
        code = (
            f"const nextSkewX = {skew_x};"
            f"const nextSkewY = {skew_y};"
            + _select_objects_js(
                f"{expr}.map(obj => {{ obj.set({{ skewX: nextSkewX, skewY: nextSkewY }}); return obj; }})"
            )
        )
        return ToolResult(
            code=code,
            description=f"Skewed {label} to ({skew_x:g}, {skew_y:g}) degrees",
            data={"object_id": object_id, "selector": selector, "skew_x": skew_x, "skew_y": skew_y},
        )

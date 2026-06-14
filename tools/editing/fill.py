import json
from typing import Any

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


GRADIENT_TYPES = ["linear", "radial"]
GRADIENT_DIRECTIONS = ["left_to_right", "top_to_bottom", "diagonal", "center_out"]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off"}
    return bool(value)


def _object_expr(object_id: str, selector: str) -> tuple[str, str] | ToolResult:
    if object_id:
        return f"canvas.getObjects().find(o => o.objectId === {_json(object_id)})", object_id
    if selector == "last":
        return "canvas.getObjects().at(-1)", "last object"
    try:
        idx = int(selector)
    except ValueError:
        return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
    return f"canvas.getObjects()[{idx}]", f"object at index {idx}"


def _objects_expr(object_id: str, selector: str) -> tuple[str, str] | ToolResult:
    if object_id:
        return f"[canvas.getObjects().find(o => o.objectId === {_json(object_id)})].filter(Boolean)", object_id
    if selector == "all":
        return "canvas.getObjects()", "all objects"
    target = _object_expr("", selector)
    if isinstance(target, ToolResult):
        return target
    expr, label = target
    return f"[{expr}].filter(Boolean)", label


def _validate_color_stops(stops: Any) -> list[dict[str, Any]]:
    if not isinstance(stops, list) or len(stops) < 2:
        raise ValueError("color_stops must include at least two stops")
    validated = []
    for index, stop in enumerate(stops):
        if not isinstance(stop, dict):
            raise ValueError(f"color_stops[{index}] must be an object")
        offset = float(stop.get("offset", index / max(1, len(stops) - 1)))
        if not 0 <= offset <= 1:
            raise ValueError(f"color_stops[{index}].offset must be between 0 and 1")
        color = str(stop.get("color", "")).strip()
        if not color:
            raise ValueError(f"color_stops[{index}].color is required")
        validated.append({"offset": offset, "color": color})
    return sorted(validated, key=lambda item: item["offset"])


class ChangeFillTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="change_fill",
            description="Apply a solid fill color to one object or a selected group of objects",
            parameters=[
                ToolParameter(name="object_id", type="string", description="Stable objectId to fill", required=False),
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="color", type="string", description="Fill color, e.g. red, transparent, or #22c55e", required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "") or ""
        selector = kwargs.get("selector", "last") or "last"
        color = str(kwargs.get("color", "")).strip()
        if not color:
            return ToolResult(is_error=True, error="color is required")
        target = _objects_expr(object_id, selector)
        if isinstance(target, ToolResult):
            return target
        expr, label = target
        code = (
            f"const fillColor = {_json(color)};"
            f"const objects = {expr};"
            "objects.forEach(obj => { obj.set({ fill: fillColor }); obj.setCoords(); });"
            "canvas.discardActiveObject();"
            "if (objects.length === 1) {"
            "  canvas.setActiveObject(objects[0]);"
            "} else if (objects.length > 1) {"
            "  canvas.setActiveObject(new fabric.ActiveSelection(objects, { canvas }));"
            "}"
            "canvas.renderAll();"
        )
        return ToolResult(
            code=code,
            description=f"Changed {label} fill to {color}",
            data={"object_id": object_id, "selector": selector, "color": color},
        )


class ApplyGradientFillTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_gradient_fill",
            description="Apply a linear or radial gradient fill to an object",
            parameters=[
                ToolParameter(name="object_id", type="string", description="Stable objectId to fill", required=False),
                ToolParameter(name="selector", type="string", description="Object selector: 'last' or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="gradient_type", type="string", description="Gradient type", required=False, enum=GRADIENT_TYPES),
                ToolParameter(name="direction", type="string", description="Gradient direction", required=False, enum=GRADIENT_DIRECTIONS),
                ToolParameter(
                    name="color_stops",
                    type="array",
                    description="Gradient color stops with offset 0..1 and color",
                    required=True,
                    min_items=2,
                    items={
                        "type": "object",
                        "properties": {
                            "offset": {"type": "number"},
                            "color": {"type": "string"},
                        },
                        "required": ["offset", "color"],
                    },
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "") or ""
        selector = kwargs.get("selector", "last") or "last"
        gradient_type = kwargs.get("gradient_type") or "linear"
        direction = kwargs.get("direction") or ("center_out" if gradient_type == "radial" else "left_to_right")
        if gradient_type not in GRADIENT_TYPES:
            return ToolResult(is_error=True, error=f"Invalid gradient_type: {gradient_type}")
        if direction not in GRADIENT_DIRECTIONS:
            return ToolResult(is_error=True, error=f"Invalid gradient direction: {direction}")
        try:
            color_stops = _validate_color_stops(kwargs.get("color_stops"))
        except (TypeError, ValueError) as exc:
            return ToolResult(is_error=True, error=str(exc))

        target = _object_expr(object_id, selector)
        if isinstance(target, ToolResult):
            return target
        expr, label = target
        stops_js = _json(color_stops)
        type_js = _json(gradient_type)
        direction_js = _json(direction)
        code = f"""
const obj = {expr};
if (obj) {{
  const bounds = obj.getBoundingRect(true, true);
  const width = Math.max(1, obj.width || bounds.width || 1);
  const height = Math.max(1, obj.height || bounds.height || 1);
  const gradientType = {type_js};
  const direction = {direction_js};
  let coords;
  if (gradientType === "radial") {{
    coords = {{ x1: 0, y1: 0, r1: 0, x2: 0, y2: 0, r2: Math.max(width, height) / 2 }};
  }} else if (direction === "top_to_bottom") {{
    coords = {{ x1: 0, y1: -height / 2, x2: 0, y2: height / 2 }};
  }} else if (direction === "diagonal") {{
    coords = {{ x1: -width / 2, y1: -height / 2, x2: width / 2, y2: height / 2 }};
  }} else {{
    coords = {{ x1: -width / 2, y1: 0, x2: width / 2, y2: 0 }};
  }}
  const gradient = new fabric.Gradient({{
    type: gradientType,
    gradientUnits: "pixels",
    coords,
    colorStops: {stops_js}
  }});
  obj.set({{ fill: gradient }});
  obj.setCoords();
  canvas.setActiveObject(obj);
  canvas.renderAll();
}}
""".strip()
        return ToolResult(
            code=code,
            description=f"Applied {gradient_type} gradient fill to {label}",
            data={
                "object_id": object_id,
                "selector": selector,
                "gradient_type": gradient_type,
                "direction": direction,
                "color_stops": color_stops,
            },
        )


class CopyObjectStyleTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="copy_object_style",
            description="Copy fill, stroke, opacity, or blend style from one object to another like an eyedropper",
            parameters=[
                ToolParameter(name="source_object_id", type="string", description="ObjectId to sample style from", required=False),
                ToolParameter(name="source_selector", type="string", description="Source selector: 'last' or index number", required=False),
                ToolParameter(name="target_object_id", type="string", description="ObjectId to apply style to", required=False),
                ToolParameter(name="target_selector", type="string", description="Target selector: 'last', 'all', or index number", required=False),
                ToolParameter(name="include_fill", type="boolean", description="Copy fill color/gradient (default: true)", required=False),
                ToolParameter(name="include_stroke", type="boolean", description="Copy stroke and strokeWidth (default: true)", required=False),
                ToolParameter(name="include_opacity", type="boolean", description="Copy opacity (default: true)", required=False),
                ToolParameter(name="include_blend_mode", type="boolean", description="Copy globalCompositeOperation (default: false)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        source_object_id = kwargs.get("source_object_id", "") or ""
        source_selector = kwargs.get("source_selector", "last") or "last"
        target_object_id = kwargs.get("target_object_id", "") or ""
        target_selector = kwargs.get("target_selector", "last") or "last"
        include_fill = _as_bool(kwargs.get("include_fill"), True)
        include_stroke = _as_bool(kwargs.get("include_stroke"), True)
        include_opacity = _as_bool(kwargs.get("include_opacity"), True)
        include_blend_mode = _as_bool(kwargs.get("include_blend_mode"), False)
        if not any([include_fill, include_stroke, include_opacity, include_blend_mode]):
            return ToolResult(is_error=True, error="At least one style part must be enabled")

        source = _object_expr(source_object_id, source_selector)
        if isinstance(source, ToolResult):
            return source
        source_expr, source_label = source
        target = _objects_expr(target_object_id, target_selector)
        if isinstance(target, ToolResult):
            return target
        target_expr, target_label = target
        assignments = []
        if include_fill:
            assignments.append("style.fill = source.fill;")
        if include_stroke:
            assignments.append("style.stroke = source.stroke; style.strokeWidth = source.strokeWidth;")
        if include_opacity:
            assignments.append("style.opacity = source.opacity;")
        if include_blend_mode:
            assignments.append("style.globalCompositeOperation = source.globalCompositeOperation;")
        code = f"""
const source = {source_expr};
const objects = {target_expr};
if (source && objects.length) {{
  const style = {{}};
  {" ".join(assignments)}
  objects.forEach(obj => {{
    obj.set(style);
    obj.setCoords();
  }});
  canvas.discardActiveObject();
  if (objects.length === 1) {{
    canvas.setActiveObject(objects[0]);
  }} else if (objects.length > 1) {{
    canvas.setActiveObject(new fabric.ActiveSelection(objects, {{ canvas }}));
  }}
  canvas.renderAll();
}}
""".strip()
        return ToolResult(
            code=code,
            description=f"Copied style from {source_label} to {target_label}",
            data={
                "source_object_id": source_object_id,
                "source_selector": source_selector,
                "target_object_id": target_object_id,
                "target_selector": target_selector,
                "include_fill": include_fill,
                "include_stroke": include_stroke,
                "include_opacity": include_opacity,
                "include_blend_mode": include_blend_mode,
            },
        )

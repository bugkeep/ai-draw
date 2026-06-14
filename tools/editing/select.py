import json

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


def _selection_code(expr: str) -> str:
    return (
        f"const selected = {expr};"
        "canvas.discardActiveObject();"
        "if (selected.length === 1) {"
        "  canvas.setActiveObject(selected[0]);"
        "} else if (selected.length > 1) {"
        "  canvas.setActiveObject(new fabric.ActiveSelection(selected, { canvas }));"
        "}"
        "canvas.renderAll();"
    )


def _filter_js(obj_type: str, color: str) -> str:
    conditions = []
    if obj_type:
        obj_type_js = json.dumps(obj_type, ensure_ascii=False)
        conditions.append(f"(obj.type === {obj_type_js} || obj.semanticType === {obj_type_js})")
    if color:
        color_js = json.dumps(color, ensure_ascii=False)
        conditions.append(f"(obj.fill === {color_js} || obj.stroke === {color_js})")
    return " && ".join(conditions) if conditions else "true"


def _target_expr(object_id: str, selector: str) -> tuple[str, str] | ToolResult:
    if object_id:
        object_id_js = json.dumps(object_id, ensure_ascii=False)
        return f"canvas.getObjects().find(o => o.objectId === {object_id_js})", object_id
    if selector == "last":
        return "canvas.getObjects().at(-1)", "last object"
    try:
        idx = int(selector)
    except ValueError:
        return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
    return f"canvas.getObjects()[{idx}]", f"object at index {idx}"


def _coerce_points(points: list) -> list[dict[str, float]]:
    coerced = []
    for point in points:
        if isinstance(point, dict):
            x = point.get("x")
            y = point.get("y")
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            x, y = point[0], point[1]
        else:
            raise ValueError("Each lasso point must include x and y")
        coerced.append({"x": float(x), "y": float(y)})
    return coerced


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
            object_id_js = json.dumps(object_id, ensure_ascii=False)
            expr = f"[canvas.getObjects().find(o => o.objectId === {object_id_js})].filter(Boolean)"
            target = object_id
        elif obj_type or color:
            expr = f"canvas.getObjects().filter(obj => {_filter_js(obj_type, color)})"
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

        code = _selection_code(expr)

        return ToolResult(
            code=code,
            description=f"Selected {target}",
            data={"object_id": object_id, "selector": selector, "type": obj_type, "color": color},
        )


class SelectByRegionTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="select_by_region",
            description="Select objects whose bounding boxes intersect or are contained within a rectangular region",
            parameters=[
                ToolParameter(name="x", type="number", description="Selection rectangle left coordinate", required=True),
                ToolParameter(name="y", type="number", description="Selection rectangle top coordinate", required=True),
                ToolParameter(name="width", type="number", description="Selection rectangle width", required=True),
                ToolParameter(name="height", type="number", description="Selection rectangle height", required=True),
                ToolParameter(name="mode", type="string", description="Selection mode", required=False, enum=["intersect", "contain"]),
                ToolParameter(name="type", type="string", description="Optional object type or semantic_type filter", required=False),
                ToolParameter(name="color", type="string", description="Optional fill or stroke color filter", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        x = float(kwargs.get("x", 0))
        y = float(kwargs.get("y", 0))
        width = float(kwargs.get("width", 0))
        height = float(kwargs.get("height", 0))
        mode = kwargs.get("mode") or "intersect"
        obj_type = kwargs.get("type", "") or ""
        color = kwargs.get("color", "") or ""
        if width <= 0 or height <= 0:
            return ToolResult(is_error=True, error="width and height must be positive")
        if mode not in ("intersect", "contain"):
            return ToolResult(is_error=True, error=f"Invalid region selection mode: {mode}")

        right = x + width
        bottom = y + height
        filter_js = _filter_js(obj_type, color)
        if mode == "contain":
            hit_test = (
                "box.left >= region.left && box.top >= region.top && "
                "box.right <= region.right && box.bottom <= region.bottom"
            )
        else:
            hit_test = (
                "box.right >= region.left && box.left <= region.right && "
                "box.bottom >= region.top && box.top <= region.bottom"
            )
        expr = f"""
canvas.getObjects().filter(obj => {{
  if (!({filter_js})) return false;
  const rect = obj.getBoundingRect(true, true);
  const region = {{ left: {x}, top: {y}, right: {right}, bottom: {bottom} }};
  const box = {{
    left: rect.left,
    top: rect.top,
    right: rect.left + rect.width,
    bottom: rect.top + rect.height
  }};
  return {hit_test};
}})
""".strip()
        return ToolResult(
            code=_selection_code(expr),
            description=f"Selected objects in {mode} region",
            data={"x": x, "y": y, "width": width, "height": height, "mode": mode, "type": obj_type, "color": color},
        )


class SelectByLassoTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="select_by_lasso",
            description="Select objects whose bounding-box centers fall inside a freehand polygon lasso",
            parameters=[
                ToolParameter(
                    name="points",
                    type="array",
                    description="Lasso polygon points as objects with x and y coordinates",
                    required=True,
                    items={
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                        },
                        "required": ["x", "y"],
                    },
                    min_items=3,
                ),
                ToolParameter(name="type", type="string", description="Optional object type or semantic_type filter", required=False),
                ToolParameter(name="color", type="string", description="Optional fill or stroke color filter", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        obj_type = kwargs.get("type", "") or ""
        color = kwargs.get("color", "") or ""
        try:
            points = _coerce_points(kwargs.get("points") or [])
        except (TypeError, ValueError) as exc:
            return ToolResult(is_error=True, error=str(exc))
        if len(points) < 3:
            return ToolResult(is_error=True, error="points must include at least three coordinates")

        points_js = json.dumps(points, ensure_ascii=False)
        filter_js = _filter_js(obj_type, color)
        expr = f"""
canvas.getObjects().filter(obj => {{
  if (!({filter_js})) return false;
  const lassoPoints = {points_js};
  const rect = obj.getBoundingRect(true, true);
  const point = {{ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }};
  let inside = false;
  for (let i = 0, j = lassoPoints.length - 1; i < lassoPoints.length; j = i++) {{
    const pi = lassoPoints[i];
    const pj = lassoPoints[j];
    const intersects = ((pi.y > point.y) !== (pj.y > point.y)) &&
      (point.x < ((pj.x - pi.x) * (point.y - pi.y)) / ((pj.y - pi.y) || 1e-9) + pi.x);
    if (intersects) inside = !inside;
  }}
  return inside;
}})
""".strip()
        return ToolResult(
            code=_selection_code(expr),
            description="Selected objects inside lasso",
            data={"points": points, "type": obj_type, "color": color},
        )


class SelectSimilarTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="select_similar",
            description="Select objects similar to a source object by type, semantic_type, fill, or stroke",
            parameters=[
                ToolParameter(name="object_id", type="string", description="Source objectId for similarity matching", required=False),
                ToolParameter(name="selector", type="string", description="Source selector: 'last' or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="match_fill", type="boolean", description="Match fill color (default: true)", required=False),
                ToolParameter(name="match_stroke", type="boolean", description="Match stroke color (default: false)", required=False),
                ToolParameter(name="match_type", type="boolean", description="Match Fabric object type (default: true)", required=False),
                ToolParameter(name="match_semantic_type", type="boolean", description="Match semanticType when available (default: false)", required=False),
                ToolParameter(name="include_source", type="boolean", description="Keep the source object in the selection (default: true)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "") or ""
        selector = kwargs.get("selector", "last") or "last"
        match_fill = kwargs.get("match_fill", True)
        match_stroke = kwargs.get("match_stroke", False)
        match_type = kwargs.get("match_type", True)
        match_semantic_type = kwargs.get("match_semantic_type", False)
        include_source = kwargs.get("include_source", True)
        match_fill = True if match_fill is None else bool(match_fill)
        match_stroke = False if match_stroke is None else bool(match_stroke)
        match_type = True if match_type is None else bool(match_type)
        match_semantic_type = False if match_semantic_type is None else bool(match_semantic_type)
        include_source = True if include_source is None else bool(include_source)
        if not any([match_fill, match_stroke, match_type, match_semantic_type]):
            return ToolResult(is_error=True, error="At least one similarity matcher must be enabled")

        target = _target_expr(object_id, selector)
        if isinstance(target, ToolResult):
            return target
        source_expr, target_name = target
        include_source_js = "true" if include_source else "false"
        checks = []
        if match_type:
            checks.append("obj.type === source.type")
        if match_semantic_type:
            checks.append("(obj.semanticType || '') === (source.semanticType || '')")
        if match_fill:
            checks.append("normalizeValue(obj.fill) === normalizeValue(source.fill)")
        if match_stroke:
            checks.append("normalizeValue(obj.stroke) === normalizeValue(source.stroke)")
        predicate = " && ".join(checks)
        expr = f"""
(() => {{
  const source = {source_expr};
  if (!source) return [];
  const normalizeValue = value => {{
    if (value === undefined || value === null) return "";
    if (typeof value === "string") return value.toLowerCase();
    return JSON.stringify(value);
  }};
  return canvas.getObjects().filter(obj => {{
    if (!{include_source_js} && obj === source) return false;
    return {predicate};
  }});
}})()
""".strip()
        return ToolResult(
            code=_selection_code(expr),
            description=f"Selected objects similar to {target_name}",
            data={
                "object_id": object_id,
                "selector": selector,
                "match_fill": match_fill,
                "match_stroke": match_stroke,
                "match_type": match_type,
                "match_semantic_type": match_semantic_type,
                "include_source": include_source,
            },
        )

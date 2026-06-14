import json
import uuid
from typing import Any

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult

POINT_SCHEMA = {
    "type": "object",
    "properties": {
        "x": {"type": "number", "description": "Canvas X coordinate"},
        "y": {"type": "number", "description": "Canvas Y coordinate"},
    },
    "required": ["x", "y"],
    "additionalProperties": False,
}


def validate_points(points: Any, *, minimum: int, maximum: int = 100) -> list[dict[str, float]]:
    if not isinstance(points, list):
        raise ValueError("points must be an array")
    if len(points) < minimum:
        raise ValueError(f"at least {minimum} points are required")
    if len(points) > maximum:
        raise ValueError(f"points cannot exceed {maximum}")

    result = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ValueError(f"points[{index}] must be an object")
        if "x" not in point or "y" not in point:
            raise ValueError(f"points[{index}] must contain x and y")
        x = float(point["x"])
        y = float(point["y"])
        if not 0 <= x <= 800:
            raise ValueError(f"points[{index}].x is outside canvas")
        if not 0 <= y <= 600:
            raise ValueError(f"points[{index}].y is outside canvas")
        result.append({"x": x, "y": y})
    return result


class DrawPolygonTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_polygon",
            description="Draw a closed polygon. Use three points for a triangle. Points are absolute canvas coordinates.",
            parameters=[
                ToolParameter(
                    name="points", type="array",
                    description="Polygon vertices in drawing order",
                    required=True, min_items=3, max_items=100,
                    items=POINT_SCHEMA,
                ),
                ToolParameter(name="fill", type="string", description="Fill color", default="#4ECDC4"),
                ToolParameter(name="stroke", type="string", description="Outline color", default="#1F2937"),
                ToolParameter(name="stroke_width", type="number", description="Outline width", default=2),
                ToolParameter(name="opacity", type="number", description="Opacity from 0 to 1", default=1),
                ToolParameter(name="object_id", type="string", description="Stable semantic object ID", required=False),
                ToolParameter(name="semantic_type", type="string", description="Semantic role e.g. roof, leaf", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        points = validate_points(kwargs["points"], minimum=3)
        fill = kwargs.get("fill", "#4ECDC4")
        stroke = kwargs.get("stroke", "#1F2937")
        stroke_width = float(kwargs.get("stroke_width", 2))
        opacity = float(kwargs.get("opacity", 1))
        object_id = kwargs.get("object_id") or f"polygon_{uuid.uuid4().hex[:8]}"
        semantic_type = kwargs.get("semantic_type", "polygon")

        if not 0 <= opacity <= 1:
            raise ValueError("opacity must be between 0 and 1")

        # Convert absolute coordinates to local coordinates for fabric
        min_x = min(p["x"] for p in points)
        min_y = min(p["y"] for p in points)
        local_points = [{"x": p["x"] - min_x, "y": p["y"] - min_y} for p in points]

        options = {
            "left": min_x, "top": min_y,
            "fill": fill, "stroke": stroke,
            "strokeWidth": stroke_width, "opacity": opacity,
            "objectCaching": True,
        }

        code = (
            f"await (async () => {{"
            f"const obj = new fabric.Polygon("
            f"{json.dumps(local_points, ensure_ascii=False)}, "
            f"{json.dumps(options, ensure_ascii=False)}"
            f");"
            f"obj.set({{objectId: '{object_id}', semanticType: '{semantic_type}'}});"
            f"canvas.add(obj); canvas.setActiveObject(obj);"
            f"}})();"
        )

        return ToolResult(
            code=code,
            description=f"Drew polygon '{object_id}' with {len(points)} points, fill={fill}",
            data={
                "type": "polygon", "object_id": object_id, "semantic_type": semantic_type,
                "points": points, "fill": fill, "stroke": stroke,
            },
        )

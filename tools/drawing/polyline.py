import json
import uuid
from typing import Any

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from .polygon import POINT_SCHEMA, validate_points


class DrawPolylineTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_polyline",
            description="Draw an open line through multiple connected points",
            parameters=[
                ToolParameter(
                    name="points", type="array",
                    description="Ordered line points in drawing order",
                    required=True, min_items=2, max_items=200,
                    items=POINT_SCHEMA,
                ),
                ToolParameter(name="stroke", type="string", description="Line color", default="#1F2937"),
                ToolParameter(name="stroke_width", type="number", description="Line width", default=3),
                ToolParameter(name="object_id", type="string", description="Stable semantic object ID", required=False),
                ToolParameter(name="semantic_type", type="string", description="Semantic role e.g. branch, outline", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        points = validate_points(kwargs["points"], minimum=2)
        stroke = kwargs.get("stroke", "#1F2937")
        stroke_width = float(kwargs.get("stroke_width", 3))
        object_id = kwargs.get("object_id") or f"polyline_{uuid.uuid4().hex[:8]}"
        semantic_type = kwargs.get("semantic_type", "polyline")

        # Convert absolute coordinates to local coordinates
        min_x = min(p["x"] for p in points)
        min_y = min(p["y"] for p in points)
        local_points = [{"x": p["x"] - min_x, "y": p["y"] - min_y} for p in points]

        options = {
            "left": min_x, "top": min_y,
            "fill": "transparent", "stroke": stroke,
            "strokeWidth": stroke_width,
            "strokeLineCap": "round", "strokeLineJoin": "round",
        }

        code = (
            f"await (async () => {{"
            f"const obj = new fabric.Polyline("
            f"{json.dumps(local_points, ensure_ascii=False)}, "
            f"{json.dumps(options, ensure_ascii=False)}"
            f");"
            f"obj.set({{objectId: '{object_id}', semanticType: '{semantic_type}'}});"
            f"canvas.add(obj);"
            f"}})();"
        )

        return ToolResult(
            code=code,
            description=f"Drew polyline '{object_id}' through {len(points)} points",
            data={
                "type": "polyline", "object_id": object_id, "semantic_type": semantic_type,
                "points": points, "stroke": stroke,
            },
        )

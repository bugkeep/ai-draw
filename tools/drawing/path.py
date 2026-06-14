import json
import uuid
import re

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult

_PATH_PATTERN = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9eE.,+\-\s]+$")


def validate_path_data(path_data: str) -> str:
    path_data = path_data.strip()
    if not path_data:
        raise ValueError("path_data cannot be empty")
    if len(path_data) > 20_000:
        raise ValueError("path_data is too long")
    if not _PATH_PATTERN.fullmatch(path_data):
        raise ValueError("path_data contains unsupported characters")
    if path_data[0] not in {"M", "m"}:
        raise ValueError("path must begin with M or m")
    return path_data


class DrawPathTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_path",
            description=(
                "Draw an SVG path. Supports M, L, H, V, C, S, Q, T, A and Z commands. "
                "Use this for curves, bezier, arcs, and complex contours. "
                "Example: 'M 100 100 C 150 20 250 180 300 100' is a cubic bezier."
            ),
            parameters=[
                ToolParameter(name="path_data", type="string", description="SVG path data string", required=True),
                ToolParameter(name="fill", type="string", description="Fill color", default="transparent"),
                ToolParameter(name="stroke", type="string", description="Stroke color", default="#1F2937"),
                ToolParameter(name="stroke_width", type="number", description="Stroke width", default=3),
                ToolParameter(name="object_id", type="string", description="Stable semantic object ID", required=False),
                ToolParameter(name="semantic_type", type="string", description="Semantic role e.g. curve, cloud", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        path_data = validate_path_data(kwargs["path_data"])
        fill = kwargs.get("fill", "transparent")
        stroke = kwargs.get("stroke", "#1F2937")
        stroke_width = float(kwargs.get("stroke_width", 3))
        object_id = kwargs.get("object_id") or f"path_{uuid.uuid4().hex[:8]}"
        semantic_type = kwargs.get("semantic_type", "path")

        code = (
            f"await (async () => {{"
            f"const obj = new fabric.Path("
            f"{json.dumps(path_data, ensure_ascii=False)}, "
            f"{{fill: {json.dumps(fill, ensure_ascii=False)}, "
            f"stroke: {json.dumps(stroke, ensure_ascii=False)}, "
            f"strokeWidth: {stroke_width}, "
            f"strokeLineCap: 'round', strokeLineJoin: 'round'}}"
            f");"
            f"obj.set({{objectId: '{object_id}', semanticType: '{semantic_type}'}});"
            f"canvas.add(obj);"
            f"}})();"
        )

        return ToolResult(
            code=code,
            description=f"Drew path '{object_id}' ({semantic_type})",
            data={
                "type": "path", "object_id": object_id, "semantic_type": semantic_type,
                "path": path_data, "fill": fill, "stroke": stroke,
            },
        )

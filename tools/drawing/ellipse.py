import random
import uuid
from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult

COLORS = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD", "#98D8C8", "#F7DC6F"]


def random_color() -> str:
    return random.choice(COLORS)


class DrawEllipseTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_ellipse",
            description="Draw an ellipse on the canvas",
            parameters=[
                ToolParameter(name="center_x", type="number", description="X coordinate of center (0-800, default: 400)", required=False),
                ToolParameter(name="center_y", type="number", description="Y coordinate of center (0-600, default: 300)", required=False),
                ToolParameter(name="rx", type="number", description="Horizontal radius (default: 80)", required=False),
                ToolParameter(name="ry", type="number", description="Vertical radius (default: 50)", required=False),
                ToolParameter(name="color", type="string", description="Fill color (default: random)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable semantic object ID", required=False),
                ToolParameter(name="semantic_type", type="string", description="Semantic role e.g. petal, leaf", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        cx = kwargs.get("center_x", 400)
        cy = kwargs.get("center_y", 300)
        rx = kwargs.get("rx", 80)
        ry = kwargs.get("ry", 50)
        color = kwargs.get("color") or random_color()
        object_id = kwargs.get("object_id") or f"ellipse_{uuid.uuid4().hex[:8]}"
        semantic_type = kwargs.get("semantic_type", "ellipse")

        code = (
            f"const obj = new fabric.Ellipse({{"
            f"left: {cx - rx}, top: {cy - ry}, "
            f"rx: {rx}, ry: {ry}, "
            f"fill: '{color}'"
            f"}});"
            f"obj.set({{objectId: '{object_id}', semanticType: '{semantic_type}'}});"
            f"canvas.add(obj); canvas.renderAll();"
        )
        description = f"Drew a {color} ellipse at ({cx}, {cy}) rx={rx} ry={ry}"
        return ToolResult(code=code, description=description, data={"type": "ellipse", "center_x": cx, "center_y": cy, "rx": rx, "ry": ry, "color": color, "object_id": object_id, "semantic_type": semantic_type})

import random
import uuid
from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult

COLORS = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD", "#98D8C8", "#F7DC6F"]


def random_color() -> str:
    return random.choice(COLORS)


class DrawCircleTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_circle",
            description="Draw a circle on the canvas",
            parameters=[
                ToolParameter(name="center_x", type="number", description="X coordinate of center (0-800, default: 400)", required=False),
                ToolParameter(name="center_y", type="number", description="Y coordinate of center (0-600, default: 300)", required=False),
                ToolParameter(name="radius", type="number", description="Radius in pixels (default: 50)", required=False),
                ToolParameter(name="color", type="string", description="Fill color (hex or name, default: random)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable semantic object ID", required=False),
                ToolParameter(name="semantic_type", type="string", description="Semantic role e.g. canopy, head", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        cx = kwargs.get("center_x", 400)
        cy = kwargs.get("center_y", 300)
        radius = kwargs.get("radius", 50)
        color = kwargs.get("color") or random_color()
        object_id = kwargs.get("object_id") or f"circle_{uuid.uuid4().hex[:8]}"
        semantic_type = kwargs.get("semantic_type", "circle")

        code = (
            f"const obj = new fabric.Circle({{"
            f"left: {cx - radius}, top: {cy - radius}, "
            f"radius: {radius}, fill: '{color}', "
            f"originX: 'center', originY: 'center'"
            f"}});"
            f"obj.set({{objectId: '{object_id}', semanticType: '{semantic_type}'}});"
            f"canvas.add(obj); canvas.renderAll();"
        )
        description = f"Drew a {color} circle at ({cx}, {cy}) with radius {radius}"
        return ToolResult(code=code, description=description, data={"type": "circle", "center_x": cx, "center_y": cy, "radius": radius, "color": color, "object_id": object_id, "semantic_type": semantic_type})

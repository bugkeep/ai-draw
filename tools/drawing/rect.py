import random
import uuid
from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult

COLORS = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD", "#98D8C8", "#F7DC6F"]


def random_color() -> str:
    return random.choice(COLORS)


class DrawRectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_rect",
            description="Draw a rectangle on the canvas",
            parameters=[
                ToolParameter(name="x", type="number", description="Left position (0-800, default: 350)", required=False),
                ToolParameter(name="y", type="number", description="Top position (0-600, default: 250)", required=False),
                ToolParameter(name="width", type="number", description="Width in pixels (default: 100)", required=False),
                ToolParameter(name="height", type="number", description="Height in pixels (default: 80)", required=False),
                ToolParameter(name="color", type="string", description="Fill color (hex or name, default: random)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable semantic object ID", required=False),
                ToolParameter(name="semantic_type", type="string", description="Semantic role e.g. wall, trunk", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        x = kwargs.get("x", 350)
        y = kwargs.get("y", 250)
        width = kwargs.get("width", 100)
        height = kwargs.get("height", 80)
        color = kwargs.get("color") or random_color()
        object_id = kwargs.get("object_id") or f"rect_{uuid.uuid4().hex[:8]}"
        semantic_type = kwargs.get("semantic_type", "rect")

        code = (
            f"const obj = new fabric.Rect({{"
            f"left: {x}, top: {y}, "
            f"width: {width}, height: {height}, "
            f"fill: '{color}'"
            f"}});"
            f"obj.set({{objectId: '{object_id}', semanticType: '{semantic_type}'}});"
            f"canvas.add(obj); canvas.renderAll();"
        )
        description = f"Drew a {color} rectangle at ({x}, {y}) {width}x{height}"
        return ToolResult(code=code, description=description, data={"type": "rect", "x": x, "y": y, "width": width, "height": height, "color": color, "object_id": object_id, "semantic_type": semantic_type})

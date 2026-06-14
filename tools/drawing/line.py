import random
from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult

COLORS = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD", "#98D8C8", "#F7DC6F"]


def random_color() -> str:
    return random.choice(COLORS)


class DrawLineTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_line",
            description="Draw a line on the canvas",
            parameters=[
                ToolParameter(name="start_x", type="number", description="Start X coordinate (default: 100)", required=False),
                ToolParameter(name="start_y", type="number", description="Start Y coordinate (default: 100)", required=False),
                ToolParameter(name="end_x", type="number", description="End X coordinate (default: 300)", required=False),
                ToolParameter(name="end_y", type="number", description="End Y coordinate (default: 300)", required=False),
                ToolParameter(name="color", type="string", description="Line color (default: random)", required=False),
                ToolParameter(name="width", type="number", description="Line width in pixels (default: 2)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        x1 = kwargs.get("start_x", 100)
        y1 = kwargs.get("start_y", 100)
        x2 = kwargs.get("end_x", 300)
        y2 = kwargs.get("end_y", 300)
        color = kwargs.get("color") or random_color()
        width = kwargs.get("width", 2)

        code = (
            f"const line = new fabric.Line([{x1}, {y1}, {x2}, {y2}], {{"
            f"stroke: '{color}', strokeWidth: {width}"
            f"}});"
            f"canvas.add(line); canvas.renderAll();"
        )
        description = f"Drew a {color} line from ({x1}, {y1}) to ({x2}, {y2})"
        return ToolResult(code=code, description=description, data={"type": "line", "start_x": x1, "start_y": y1, "end_x": x2, "end_y": y2, "color": color})

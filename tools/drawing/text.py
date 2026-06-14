import random
import uuid
from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult

COLORS = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD", "#98D8C8", "#F7DC6F"]


def random_color() -> str:
    return random.choice(COLORS)


class DrawTextTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_text",
            description="Draw text on the canvas",
            parameters=[
                ToolParameter(name="x", type="number", description="X position (0-800, default: 100)", required=False),
                ToolParameter(name="y", type="number", description="Y position (0-600, default: 100)", required=False),
                ToolParameter(name="text", type="string", description="Text content to draw", required=True),
                ToolParameter(name="font_size", type="number", description="Font size in pixels (default: 24)", required=False),
                ToolParameter(name="color", type="string", description="Text color (default: random)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable semantic object ID", required=False),
                ToolParameter(name="semantic_type", type="string", description="Semantic role e.g. label, title", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        x = kwargs.get("x", 100)
        y = kwargs.get("y", 100)
        text = kwargs.get("text", "")
        font_size = kwargs.get("font_size", 24)
        color = kwargs.get("color") or random_color()
        object_id = kwargs.get("object_id") or f"text_{uuid.uuid4().hex[:8]}"
        semantic_type = kwargs.get("semantic_type", "text")

        if not text:
            return ToolResult(is_error=True, error="Text content is required")

        escaped = text.replace("'", "\\'")
        code = (
            f"const obj = new fabric.Text('{escaped}', {{"
            f"left: {x}, top: {y}, "
            f"fontSize: {font_size}, fill: '{color}'"
            f"}});"
            f"obj.set({{objectId: '{object_id}', semanticType: '{semantic_type}'}});"
            f"canvas.add(obj); canvas.renderAll();"
        )
        description = f"Drew text '{text}' at ({x}, {y}) with size {font_size}"
        return ToolResult(code=code, description=description, data={"type": "text", "x": x, "y": y, "text": text, "font_size": font_size, "color": color, "object_id": object_id, "semantic_type": semantic_type})

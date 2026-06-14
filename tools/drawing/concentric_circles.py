import json
import uuid
from typing import Any

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


LAYER_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Layer role, e.g. outer, middle, inner"},
        "radius": {"type": "number", "description": "Circle radius in pixels"},
        "color": {"type": "string", "description": "Fill color, e.g. blue or #1677ff"},
    },
    "required": ["radius", "color"],
    "additionalProperties": False,
}


def _validate_layers(layers: Any) -> list[dict[str, Any]]:
    if layers is None:
        return []
    if not isinstance(layers, list):
        raise ValueError("layers must be an array")
    if len(layers) < 2:
        raise ValueError("concentric circles require at least two layers")
    if len(layers) > 12:
        raise ValueError("concentric circles cannot exceed 12 layers")

    validated = []
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise ValueError(f"layers[{index}] must be an object")
        radius = float(layer.get("radius", 0))
        if radius <= 0 or radius > 300:
            raise ValueError(f"layers[{index}].radius must be between 1 and 300")
        color = str(layer.get("color", "")).strip()
        if not color:
            raise ValueError(f"layers[{index}].color is required")
        name = str(layer.get("name") or f"layer_{index + 1}").strip()
        validated.append({"name": name, "radius": radius, "color": color})
    return sorted(validated, key=lambda item: item["radius"], reverse=True)


class DrawConcentricCirclesTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_concentric_circles",
            description=(
                "Draw two or more concentric circles that share exactly one center. "
                "Use this for concentric circles, nested circles, target/bullseye, "
                "and requests such as 'inner circle green, outer circle blue'. "
                "The tool draws larger outer layers first and smaller inner layers last."
            ),
            parameters=[
                ToolParameter(name="center_x", type="number", description="Shared center X coordinate (0-800, default: 400)", required=False),
                ToolParameter(name="center_y", type="number", description="Shared center Y coordinate (0-600, default: 300)", required=False),
                ToolParameter(name="outer_radius", type="number", description="Outer circle radius when layers is omitted (default: 120)", required=False),
                ToolParameter(name="inner_radius", type="number", description="Inner circle radius when layers is omitted (default: 60)", required=False),
                ToolParameter(name="outer_color", type="string", description="Outer circle fill color (default: blue)", required=False),
                ToolParameter(name="inner_color", type="string", description="Inner circle fill color (default: green)", required=False),
                ToolParameter(
                    name="layers", type="array",
                    description=(
                        "Optional explicit layers. Each layer has radius, color, and optional name. "
                        "They will be sorted by radius descending so all circles share one center."
                    ),
                    required=False, min_items=2, max_items=12,
                    items=LAYER_SCHEMA,
                ),
                ToolParameter(name="stroke", type="string", description="Circle outline color (default: transparent)", required=False),
                ToolParameter(name="stroke_width", type="number", description="Outline width in pixels (default: 0)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable base object ID", required=False),
                ToolParameter(name="semantic_type", type="string", description="Semantic role, default: concentric_circles", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        center_x = float(kwargs.get("center_x", 400))
        center_y = float(kwargs.get("center_y", 300))
        stroke = kwargs.get("stroke", "transparent")
        stroke_width = float(kwargs.get("stroke_width", 0))
        object_id = kwargs.get("object_id") or f"concentric_{uuid.uuid4().hex[:8]}"
        semantic_type = kwargs.get("semantic_type", "concentric_circles")

        if not 0 <= center_x <= 800 or not 0 <= center_y <= 600:
            raise ValueError("center must be inside the 800x600 canvas")
        if stroke_width < 0 or stroke_width > 40:
            raise ValueError("stroke_width must be between 0 and 40")

        layers = _validate_layers(kwargs.get("layers"))
        if not layers:
            outer_radius = float(kwargs.get("outer_radius", 120))
            inner_radius = float(kwargs.get("inner_radius", 60))
            if outer_radius <= inner_radius:
                raise ValueError("outer_radius must be larger than inner_radius")
            if inner_radius <= 0 or outer_radius > 300:
                raise ValueError("radii must be between 1 and 300")
            layers = [
                {
                    "name": "outer",
                    "radius": outer_radius,
                    "color": kwargs.get("outer_color", "blue"),
                },
                {
                    "name": "inner",
                    "radius": inner_radius,
                    "color": kwargs.get("inner_color", "green"),
                },
            ]

        layer_js = []
        for index, layer in enumerate(layers):
            layer_id = f"{object_id}_{layer['name']}"
            options = {
                "left": center_x,
                "top": center_y,
                "radius": layer["radius"],
                "fill": layer["color"],
                "stroke": stroke,
                "strokeWidth": stroke_width,
                "originX": "center",
                "originY": "center",
            }
            layer_js.append(
                "const obj{index} = new fabric.Circle({options});"
                "obj{index}.set({{objectId: {layer_id}, semanticType: {semantic_type}}});"
                "canvas.add(obj{index});"
                "parts.push(obj{index});".format(
                    index=index,
                    options=json.dumps(options, ensure_ascii=False),
                    layer_id=json.dumps(layer_id, ensure_ascii=False),
                    semantic_type=json.dumps(f"{semantic_type}_{layer['name']}", ensure_ascii=False),
                )
            )

        code = (
            "await (async () => {"
            "const parts = [];"
            + "".join(layer_js)
            + "if (parts.length) {"
            + "const selection = new fabric.ActiveSelection(parts, { canvas });"
            + "canvas.setActiveObject(selection);"
            + "}"
            + "canvas.renderAll();"
            + "})();"
        )

        return ToolResult(
            code=code,
            description=(
                f"Drew concentric circles '{object_id}' at "
                f"({center_x:g}, {center_y:g}) with {len(layers)} layers"
            ),
            data={
                "type": "concentric_circles",
                "center_x": center_x,
                "center_y": center_y,
                "layers": layers,
                "object_id": object_id,
                "semantic_type": semantic_type,
            },
        )

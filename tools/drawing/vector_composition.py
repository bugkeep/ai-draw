"""Draw a detailed editable vector composition from sanitized inline SVG."""

from __future__ import annotations

import json
import uuid
from xml.etree import ElementTree as ET

from assets.services.svg_sanitizer import sanitize_svg
from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


VISIBLE_TAGS = {"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}
MAX_INLINE_SVG_CHARS = 50_000


class DrawVectorCompositionTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_vector_composition",
            description=(
                "Draw one detailed, coherent editable vector composition from inline SVG. "
                "Use for complex single subjects or scenes that need perspective, curved silhouettes, "
                "layered parts, gradients, highlights, and shadows. The SVG is sanitized before rendering. "
                "Prefer 10+ visible elements for a detailed subject."
            ),
            parameters=[
                ToolParameter(
                    name="svg",
                    type="string",
                    description=(
                        "Complete inline SVG with a viewBox. May contain safe paths, polygons, ellipses, "
                        "rects, groups, gradients, masks, and filters. No scripts or external references."
                    ),
                    required=True,
                ),
                ToolParameter(name="object_id", type="string", description="Stable object ID", required=False),
                ToolParameter(name="semantic_type", type="string", description="Semantic role", required=False),
                ToolParameter(name="left", type="number", description="Canvas X position", default=80),
                ToolParameter(name="top", type="number", description="Canvas Y position", default=80),
                ToolParameter(name="width", type="number", description="Rendered width", default=640),
                ToolParameter(name="height", type="number", description="Rendered height", default=440),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        svg = str(kwargs.get("svg", "")).strip()
        if not svg:
            return ToolResult(is_error=True, error="svg is required", error_type="invalid_args")
        if len(svg) > MAX_INLINE_SVG_CHARS:
            return ToolResult(
                is_error=True,
                error=f"svg exceeds {MAX_INLINE_SVG_CHARS} characters",
                error_type="invalid_args",
            )

        try:
            sanitized = sanitize_svg(svg.encode("utf-8"))
            root = ET.fromstring(sanitized)
        except Exception as exc:
            return ToolResult(
                is_error=True,
                error=f"invalid SVG composition: {exc}",
                error_type="invalid_args",
            )

        visible_elements = sum(1 for element in root.iter() if element.tag.lower() in VISIBLE_TAGS)
        if visible_elements < 3:
            return ToolResult(
                is_error=True,
                error="vector composition requires at least 3 visible SVG elements",
                error_type="invalid_args",
            )

        object_id = kwargs.get("object_id") or f"composition_{uuid.uuid4().hex[:8]}"
        semantic_type = kwargs.get("semantic_type") or "vector_composition"
        left = float(kwargs.get("left", 80))
        top = float(kwargs.get("top", 80))
        width = float(kwargs.get("width", 640))
        height = float(kwargs.get("height", 440))
        if width <= 0 or height <= 0:
            return ToolResult(is_error=True, error="width and height must be positive", error_type="invalid_args")

        svg_text = sanitized.decode("utf-8")
        code = "\n".join([
            f"const compositionSvg = {json.dumps(svg_text, ensure_ascii=False)};",
            "const compositionData = await new Promise((resolve, reject) => {",
            "  fabric.loadSVGFromString(compositionSvg, function(objects, options) {",
            "    if (objects && objects.length > 0) resolve({ objects, options });",
            "    else reject(new Error('SVG composition returned no objects'));",
            "  });",
            "});",
            "const composition = fabric.util.groupSVGElements(compositionData.objects, compositionData.options);",
            "composition.set({",
            f"  left: {left}, top: {top},",
            f"  scaleX: {width} / (composition.width || 1),",
            f"  scaleY: {height} / (composition.height || 1),",
            f"  objectId: {json.dumps(str(object_id), ensure_ascii=False)},",
            f"  semanticType: {json.dumps(str(semantic_type), ensure_ascii=False)},",
            "});",
            "canvas.add(composition);",
            "canvas.setActiveObject(composition);",
            "canvas.requestRenderAll();",
        ])

        return ToolResult(
            code=code,
            description=(
                f"Drew detailed vector composition '{object_id}' "
                f"with {visible_elements} visible elements"
            ),
            data={
                "type": "vector_composition",
                "object_id": object_id,
                "semantic_type": semantic_type,
                "visible_elements": visible_elements,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            },
        )

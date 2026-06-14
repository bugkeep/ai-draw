"""Tool: replace_vector_asset — replace an imported asset on the canvas.

Preserves position, size, rotation, object_id, and semantic identity.
Uses ``import_vector_asset`` internally, then generates JS to swap
the fabric object.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from tools.assets.import_vector_asset import ImportVectorAssetTool

logger = logging.getLogger(__name__)


class ReplaceVectorAssetTool(BaseTool):
    """Replace an existing canvas asset with a new SVG candidate.

    The replacement preserves the original object's position, size,
    rotation, and semantic identity (object_id stays the same).
    """

    def __init__(self):
        self._import_tool = ImportVectorAssetTool()

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="replace_vector_asset",
            description="Replace an existing imported asset on the canvas with a different SVG. "
                        "Preserves position, size, rotation, and object_id.",
            parameters=[
                ToolParameter(
                    name="object_id",
                    type="string",
                    description="object_id of the existing asset to replace",
                    required=True,
                ),
                ToolParameter(
                    name="asset_id",
                    type="string",
                    description="New asset identifier from search_vector_asset, e.g. 'iconify:mdi:emoticon-happy'",
                    required=True,
                ),
                ToolParameter(
                    name="fill",
                    type="string",
                    description="Optional fill color override (hex or name). Only affects single-color SVGs.",
                    required=False,
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id: str = kwargs.get("object_id", "")
        asset_id: str = kwargs.get("asset_id", "")
        fill: str | None = kwargs.get("fill")

        if not object_id:
            return ToolResult(is_error=True, error="object_id is required", error_type="invalid_args")
        if not asset_id:
            return ToolResult(is_error=True, error="asset_id is required", error_type="invalid_args")

        # Import the new asset (get cached SVG)
        import_result = self._import_tool.execute(
            asset_id=asset_id,
            object_id=f"__replace_temp_{__import__('uuid').uuid4().hex[:6]}__",
            semantic_type="temp_replace",
            left=0,
            top=0,
            width=200,
            height=200,
        )

        if import_result.is_error:
            return import_result

        import_data = import_result.data or {}
        cache_key = import_data.get("cache_key", "")
        asset_url = import_data.get("asset_url", "")

        if not cache_key:
            return ToolResult(
                is_error=True,
                error="Failed to resolve cache key for replacement asset",
                error_type="execution_error",
            )

        escaped_url = asset_url.replace("'", "\\'")
        escaped_object_id = object_id.replace("'", "\\'")
        fill_code = ""

        if fill:
            escaped_fill = fill.replace("'", "\\'")
            fill_code = (
                f"    if (obj.fill && obj.fill !== 'none') obj.set('fill', '{escaped_fill}');\n"
                f"    if (obj.stroke && obj.stroke !== 'none') obj.set('stroke', '{escaped_fill}');\n"
            )

        js = (
            f"// Replace asset for object '{escaped_object_id}'\n"
            f"const oldObj = canvas.getObjects().find(o => o.objectId === '{escaped_object_id}');\n"
            f"if (!oldObj) {{\n"
            f"  console.warn('Object not found: {escaped_object_id}');\n"
            f"}} else {{\n"
            f"  const oldTransform = {{\n"
            f"    left: oldObj.left,\n"
            f"    top: oldObj.top,\n"
            f"    scaleX: oldObj.scaleX,\n"
            f"    scaleY: oldObj.scaleY,\n"
            f"    angle: oldObj.angle,\n"
            f"    opacity: oldObj.opacity,\n"
            f"  }};\n"
            f"  const svgData = await new Promise((resolve, reject) => {{\n"
            f"    fabric.loadSVGFromURL('{escaped_url}', function(objects, options) {{\n"
            f"      if (objects && objects.length > 0) resolve({{ objects, options }});\n"
            f"      else reject(new Error('No objects'));\n"
            f"    }}, function(err) {{ reject(err); }});\n"
            f"  }});\n"
            f"  const newGroup = fabric.util.groupSVGElements(svgData.objects, svgData.options);\n"
            f"  newGroup.set({{\n"
            f"    left: oldTransform.left,\n"
            f"    top: oldTransform.top,\n"
            f"    scaleX: oldTransform.scaleX,\n"
            f"    scaleY: oldTransform.scaleY,\n"
            f"    angle: oldTransform.angle,\n"
            f"    opacity: oldTransform.opacity,\n"
            f"    objectId: '{escaped_object_id}',\n"
            f"    semanticType: oldObj.semanticType,\n"
            f"  }});\n"
            f"{fill_code}"
            f"  canvas.remove(oldObj);\n"
            f"  canvas.add(newGroup);\n"
            f"  canvas.setActiveObject(newGroup);\n"
            f"  canvas.requestRenderAll();\n"
            f"}}\n"
        )

        description = f"Replaced asset for '{object_id}' with '{asset_id}' (position and size preserved)"

        return ToolResult(
            code=js,
            description=description,
            data={
                "object_id": object_id,
                "asset_id": asset_id,
                "cache_key": cache_key,
            },
        )

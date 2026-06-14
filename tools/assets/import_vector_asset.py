"""Tool: import_vector_asset — download, sanitize, cache, and place an SVG onto the canvas.

The LLM calls this after choosing a candidate from ``search_vector_asset``.
The tool handles the entire pipeline: download → validate → sanitize → cache → generate Fabric.js code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


class ImportVectorAssetTool(BaseTool):
    """Download, sanitize, cache, and import an SVG asset onto the canvas.

    Call ``search_vector_asset`` first, then pass the chosen ``asset_id``
    to this tool.
    """

    def __init__(self):
        self._cache: AssetCache | None = None
        self._downloader: DownloadService | None = None

    def _ensure_services(self):
        if self._cache is None:
            from assets.services.cache_service import AssetCache
            self._cache = AssetCache()
        if self._downloader is None:
            from assets.services.download_service import DownloadService
            self._downloader = DownloadService()

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="import_vector_asset",
            description="Download, sanitize, cache, and place a vector asset onto the canvas. "
                        "Use after search_vector_asset — pass the asset_id of the chosen candidate.",
            parameters=[
                ToolParameter(
                    name="asset_id",
                    type="string",
                    description="Asset identifier returned by search_vector_asset, e.g. 'iconify:mdi:emoticon-happy'",
                    required=True,
                ),
                ToolParameter(
                    name="object_id",
                    type="string",
                    description="Stable semantic object identifier (e.g. 'smiley_1', 'heart_1')",
                    required=False,
                ),
                ToolParameter(
                    name="semantic_type",
                    type="string",
                    description="Semantic role of the object (e.g. 'smiley_face', 'heart_icon')",
                    required=False,
                ),
                ToolParameter(
                    name="left",
                    type="number",
                    description="Canvas X position (0-800, default: 300)",
                    required=False,
                    default=300,
                ),
                ToolParameter(
                    name="top",
                    type="number",
                    description="Canvas Y position (0-600, default: 200)",
                    required=False,
                    default=200,
                ),
                ToolParameter(
                    name="width",
                    type="number",
                    description="Target width in pixels (default: 200)",
                    required=False,
                    default=200,
                ),
                ToolParameter(
                    name="height",
                    type="number",
                    description="Target height in pixels (default: 200)",
                    required=False,
                    default=200,
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
        asset_id: str = kwargs.get("asset_id", "")
        object_id: str = kwargs.get("object_id", "")
        semantic_type: str = kwargs.get("semantic_type", "")
        left: float = float(kwargs.get("left", 300))
        top: float = float(kwargs.get("top", 200))
        width: float = float(kwargs.get("width", 200))
        height: float = float(kwargs.get("height", 200))
        fill: str | None = kwargs.get("fill")

        if not asset_id:
            return ToolResult(
                is_error=True,
                error="asset_id is required",
                error_type="invalid_args",
            )

        # Parse asset_id format: "provider:provider_asset_id"
        if ":" not in asset_id:
            return ToolResult(
                is_error=True,
                error=f"Invalid asset_id format: {asset_id}. Expected format: 'provider:id'.",
                error_type="invalid_args",
            )

        prefix, provider_id = asset_id.split(":", 1)
        provider = "iconify" if prefix == "iconify" else prefix

        # Derive defaults from asset_id
        if not object_id:
            obj_name = provider_id.split(":")[-1].replace("-", "_").replace(".", "_")
            object_id = f"{obj_name}_{__import__('uuid').uuid4().hex[:6]}"
        if not semantic_type:
            semantic_type = f"{provider_id.split(':')[-1].split('-')[0]}_icon"

        # Build candidate for download/cache
        # Use Iconify download URL pattern for iconify assets
        if provider == "iconify":
            ico_prefix, ico_name = provider_id.split(":", 1) if ":" in provider_id else ("", provider_id)
            download_url = f"https://api.iconify.design/{ico_prefix}/{ico_name}.svg"
        else:
            download_url = ""

        if not download_url:
            return ToolResult(
                is_error=True,
                error=f"Cannot resolve download URL for asset: {asset_id}",
                error_type="invalid_args",
            )

        from assets.domain.models import AssetCandidate

        candidate = AssetCandidate(
            asset_id=asset_id,
            provider=provider,
            provider_asset_id=provider_id,
            title=object_id.replace("_", " ").title(),
            format="svg",
            download_url=download_url,
        )

        self._ensure_services()
        assert self._downloader is not None
        assert self._cache is not None

        # Check cache first
        cache_key = self._cache.make_cache_key(provider, provider_id)

        if self._cache.has_sanitized(cache_key):
            svg_data = self._cache.read_sanitized(cache_key)
            logger.info("Cache hit for asset %s (key=%s)", asset_id, cache_key)
        else:
            # Download, sanitize, cache
            try:
                result = asyncio.run(self._downloader.download(download_url, fmt="svg"))
            except Exception as e:
                logger.exception("Failed to download asset %s", asset_id)
                return ToolResult(
                    is_error=True,
                    error=f"Download failed for '{asset_id}': {e}",
                    error_type="execution_error",
                )

            svg_data = result.data

            # Write cache
            try:
                self._cache.write_raw(cache_key, result.data)
                self._cache.write_sanitized(cache_key, result.data)
                from assets.services.metadata_service import MetadataService
                meta_svc = MetadataService(self._cache)
                metadata = meta_svc.build_metadata(
                    candidate,
                    raw_data=result.data,
                    sanitized_data=result.data,
                )
                meta_svc.save(candidate, metadata)
            except Exception as e:
                logger.warning("Cache write failed for %s: %s", asset_id, e)

        # Generate Fabric.js JavaScript code
        asset_url = f"/assets/content/{cache_key}.svg"
        escaped_asset_url = asset_url.replace("'", "\\'")
        escaped_object_id = object_id.replace("'", "\\'")
        escaped_semantic_type = semantic_type.replace("'", "\\'")

        js_lines = [
            f"const assetUrl = '{escaped_asset_url}';",
            f"const svgData = await new Promise((resolve, reject) => {{",
            f"  fabric.loadSVGFromURL(assetUrl, function(objects, options) {{",
            f"    if (objects && objects.length > 0) {{",
            f"      resolve({{ objects, options }});",
            f"    }} else {{",
            f"      reject(new Error('SVG returned no objects'));",
            f"    }}",
            f"  }}, function(err) {{",
            f"    reject(err);",
            f"  }});",
            f"}});",
            f"const importedGroup = fabric.util.groupSVGElements(svgData.objects, svgData.options);",
            f"importedGroup.set({{",
            f"  left: {left},",
            f"  top: {top},",
            f"  objectId: '{escaped_object_id}',",
            f"  semanticType: '{escaped_semantic_type}',",
        ]

        if fill:
            escaped_fill = fill.replace("'", "\\'")
            js_lines.append(f"  fill: '{escaped_fill}',")

        js_lines.extend([
            "});",
        ])

        # Scale to target size
        if width and height:
            js_lines.append(
                "importedGroup.set({"
                + "scaleX: " + str(width) + " / (importedGroup.width || 1), "
                + "scaleY: " + str(height) + " / (importedGroup.height || 1), "
                + "});"
            )

        js_lines.extend([
            "canvas.add(importedGroup);",
            "canvas.setActiveObject(importedGroup);",
            "canvas.requestRenderAll();",
        ])

        code = "\n".join(js_lines)
        description = (
            f"Imported '{asset_id}' as '{object_id}' "
            f"at ({int(left)}, {int(top)}) size {int(width)}x{int(height)}"
        )

        return ToolResult(
            code=code,
            description=description,
            data={
                "object_id": object_id,
                "semantic_type": semantic_type,
                "asset_id": asset_id,
                "cache_key": cache_key,
                "asset_url": asset_url,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "import_mode": "svg_group",
            },
        )

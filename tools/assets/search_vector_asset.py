"""Tool: search_vector_asset — search SVG for common icons and objects.

The LLM calls this to find SVG candidates.  Results are cached in-memory
so ``list_asset_candidates`` can return them without re-searching.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

# In-memory cache for the most recent search results per search_id.
_SEARCH_CACHE: dict[str, dict[str, Any]] = {}
_LATEST_SEARCH_ID: str = ""


class SearchVectorAssetTool(BaseTool):
    """Search for SVG assets matching a query.

    Returns ranked candidates with scores, preview URLs, and license info.
    The LLM reviews the candidates and calls ``import_vector_asset`` to
    place one onto the canvas.
    """

    def __init__(self):
        self._search_service: SearchService | None = None

    def _ensure_service(self):
        if self._search_service is None:
            from assets.services.ranking_service import RankingService
            from assets.services.search_service import SearchService
            from assets.providers.iconify_provider import IconifyProvider
            from assets.providers.local_provider import LocalAssetProvider
            ranking = RankingService()
            svc = SearchService(ranking=ranking)
            svc.register(IconifyProvider())
            svc.register(LocalAssetProvider())
            self._search_service = svc

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="search_vector_asset",
            description="Search for SVG icons and cartoon objects matching the user's description. "
                        "Returns ranked candidates with scores, preview URLs, and license info.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Search query describing the desired icon or object (e.g. 'smiling face', 'red car')",
                    required=True,
                ),
                ToolParameter(
                    name="style",
                    type="string",
                    description="Desired style: flat, outline, cartoon, minimal, realistic (optional)",
                    required=False,
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum number of candidates to return (1-20, default 8)",
                    required=False,
                    default=8,
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        global _LATEST_SEARCH_ID

        query = kwargs.get("query", "")
        style = kwargs.get("style", "")
        limit = min(int(kwargs.get("limit", 8)), 20)

        if not query:
            return ToolResult(
                is_error=True,
                error="query is required",
                error_type="invalid_args",
            )

        self._ensure_service()

        from assets.domain.models import AssetSearchRequest

        try:
            result = asyncio.run(self._search_service.search(
                AssetSearchRequest(query=query, style=style, limit=limit)
            ))
        except Exception as e:
            logger.exception("search_vector_asset failed for query=%s", query)
            return ToolResult(
                is_error=True,
                error=f"Search failed: {e}",
                error_type="execution_error",
            )

        _LATEST_SEARCH_ID = result.search_id

        # Build structured data for frontend / future use
        candidates_data = [
            {
                "asset_id": c.asset_id,
                "title": c.title,
                "provider": c.provider,
                "score": round(c.final_score, 3),
                "preview_url": c.preview_url or "",
                "license": c.license_name or "",
                "format": c.format,
                "width": c.width,
                "height": c.height,
            }
            for c in result.candidates
        ]

        # Cache for list_asset_candidates
        _SEARCH_CACHE[result.search_id] = {
            "query": query,
            "style": style,
            "candidates": candidates_data,
        }

        # Clean old cache entries (keep last 5)
        while len(_SEARCH_CACHE) > 5:
            oldest = next(iter(_SEARCH_CACHE))
            del _SEARCH_CACHE[oldest]

        if not candidates_data:
            return ToolResult(
                data={"search_id": result.search_id, "candidates": []},
                description=f"No assets found for '{query}'.  Try a different query or fall back to primitive shapes.",
            )

        # Build human-readable description for the LLM
        desc_lines = [
            f"Found {len(candidates_data)} candidates for '{query}':",
        ]
        for i, c in enumerate(candidates_data[:10], 1):
            score_str = f"{c['score']:.0%}" if c["score"] < 1 else "99%"
            desc_lines.append(
                f"  {i}. [{c['asset_id']}] {c['title']} "
                f"(score={score_str}, provider={c['provider']}, license={c['license']})"
            )
        desc_lines.append(f"\nSearch ID: {result.search_id}")
        desc_lines.append("Use import_vector_asset(asset_id='...') to place a candidate on the canvas.")

        return ToolResult(
            data={
                "search_id": result.search_id,
                "normalized_query": result.normalized_query,
                "candidates": candidates_data,
                "provider_results": result.provider_results,
            },
            description="\n".join(desc_lines),
        )


def get_search_cache() -> dict[str, dict[str, Any]]:
    """Exposed for ``list_asset_candidates`` tool."""
    return _SEARCH_CACHE


def get_latest_search_id() -> str:
    """Exposed for ``list_asset_candidates`` tool."""
    return _LATEST_SEARCH_ID

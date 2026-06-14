"""Tool: list_asset_candidates — show previous search results.

Returns the last set of candidates from ``search_vector_asset`` without
re-searching.  Used when the user says "show me other options" or
"try a different one".
"""

from __future__ import annotations

import logging

from tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from tools.assets.search_vector_asset import get_search_cache, get_latest_search_id

logger = logging.getLogger(__name__)


class ListAssetCandidatesTool(BaseTool):
    """List candidates from the most recent ``search_vector_asset`` call.

    Does NOT perform a new search — returns cached results so the LLM
    can pick a different candidate without extra API calls.
    """

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_asset_candidates",
            description="List candidates from the most recent search_vector_asset call. "
                        "Use this when the user wants to see other options or try a different one.",
            parameters=[
                ToolParameter(
                    name="search_id",
                    type="string",
                    description="Optional: specific search ID to list (default: most recent)",
                    required=False,
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        search_id: str | None = kwargs.get("search_id")

        cache = get_search_cache()
        if not cache:
            return ToolResult(
                data={"candidates": []},
                description="No recent searches found.  Use search_vector_asset first.",
            )

        if search_id and search_id not in cache:
            return ToolResult(
                is_error=True,
                error=f"Search ID '{search_id}' not found",
                error_type="not_found",
            )

        if not search_id:
            latest = get_latest_search_id()
            entry = cache.get(latest)
            if not entry:
                # Fall back to any entry
                if cache:
                    search_id = next(iter(cache))
                    entry = cache[search_id]
                else:
                    return ToolResult(
                        data={"candidates": []},
                        description="No recent searches found.",
                    )
            else:
                search_id = latest
        else:
            entry = cache[search_id]

        candidates = entry.get("candidates", [])
        if not candidates:
            return ToolResult(
                data={"search_id": search_id, "candidates": []},
                description=f"No candidates in search '{search_id}'.",
            )

        lines = [
            f"Candidates from search '{search_id}' (query: '{entry.get('query', '')}'):",
        ]
        for i, c in enumerate(candidates, 1):
            score_str = f"{c['score']:.0%}" if c['score'] < 1 else "99%"
            lines.append(
                f"  {i}. [{c['asset_id']}] {c['title']} "
                f"(score={score_str}, provider={c['provider']})"
            )
        lines.append("\nUse import_vector_asset(asset_id='...') to place a different candidate.")

        return ToolResult(
            data={"search_id": search_id, "query": entry.get("query", ""), "candidates": candidates},
            description="\n".join(lines),
        )

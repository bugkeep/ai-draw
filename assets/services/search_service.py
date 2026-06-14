"""Search service — orchestrates multiple providers and ranks results.

Flow::

    1. Normalise query (expand synonyms, translate)
    2. Query all registered providers in parallel
    3. Deduplicate results by provider_asset_id
    4. Rank candidates with RankingService
    5. Return top-N + diagnostics
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from copy import deepcopy
from typing import Any

from assets.domain.models import AssetSearchRequest, AssetCandidate
from assets.domain.enums import AssetErrorCode
from assets.domain.errors import AssetError
from assets.providers.base import AssetProvider
from assets.services.ranking_service import RankingService

logger = logging.getLogger(__name__)


class SearchResult:
    """Result of a multi-provider search."""

    def __init__(self, search_id: str, normalized_query: str,
                 candidates: list[AssetCandidate],
                 provider_results: dict[str, int],
                 errors: list[AssetError]):
        self.search_id = search_id
        self.normalized_query = normalized_query
        self.candidates = candidates
        self.provider_results = provider_results  # provider_name → count
        self.errors = errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "search_id": self.search_id,
            "normalized_query": self.normalized_query,
            "candidate_count": len(self.candidates),
            "provider_results": self.provider_results,
            "errors": [{"code": e.code.value, "message": str(e)} for e in self.errors],
        }


def _make_search_id(query: str) -> str:
    raw = f"search_{query}_{__import__('time').time()}"
    return "search_" + hashlib.md5(raw.encode()).hexdigest()[:12]


# Simple query normalisation for Chinese → English icon queries
_COMMON_TRANSLATIONS = {
    "笑脸": "smiling face",
    "哭脸": "crying face",
    "爱心": "heart",
    "心形": "heart",
    "汽车": "car",
    "自行车": "bicycle",
    "飞机": "airplane",
    "火车": "train",
    "猫": "cat",
    "狗": "dog",
    "兔子": "rabbit",
    "小鸟": "bird",
    "鱼": "fish",
    "蝴蝶": "butterfly",
    "蜜蜂": "bee",
    "熊猫": "panda",
    "老虎": "tiger",
    "狮子": "lion",
    "云": "cloud",
    "太阳": "sun",
    "月亮": "moon",
    "星星": "star",
    "彩虹": "rainbow",
    "闪电": "lightning",
    "树": "tree",
    "花朵": "flower",
    "花": "flower",
    "房子": "house",
    "礼物": "gift",
    "星星": "star",
    "太阳": "sun",
    "月亮": "moon",
}


def _normalize_query(query: str) -> tuple[str, list[str]]:
    """Convert the primary query to English and generate expanded queries.

    Returns (primary_english_query, [expanded_queries]).
    """
    # If already mostly English, use as-is
    if query.isascii() and any(c.isalpha() for c in query):
        return query, [query]

    # Try common translations
    for zh, en in _COMMON_TRANSLATIONS.items():
        if zh in query:
            return en, [en, query]

    # If query contains Chinese but no exact translation, return as-is
    return query, [query]


class SearchService:
    """Orchestrates asset search across multiple providers."""

    def __init__(self, ranking: RankingService | None = None):
        self._providers: list[AssetProvider] = []
        self._ranking = ranking or RankingService()

    def register(self, provider: AssetProvider):
        """Add a provider to the search pool (respects registration order)."""
        self._providers.append(provider)

    async def search(self, request: AssetSearchRequest) -> SearchResult:
        """Execute a multi-provider search with ranking."""
        # Normalise
        primary_query, expanded = _normalize_query(request.query)
        request.normalized_queries = expanded[:4]  # cap at 4

        queries_to_try = [primary_query] + expanded[1:4]

        all_candidates: list[AssetCandidate] = []
        seen_ids: set[str] = set()
        provider_counts: dict[str, int] = {}
        errors: list[AssetError] = []

        for query in queries_to_try:
            if len(all_candidates) >= request.limit:
                break

            # Query providers in parallel
            tasks = [self._search_provider(p, query, request) for p in self._providers]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for provider, result in zip(self._providers, results):
                if isinstance(result, Exception):
                    logger.warning("Provider %s failed for query=%s: %s",
                                   provider.name, query, result)
                    errors.append(AssetError(
                        AssetErrorCode.PROVIDER_UNAVAILABLE,
                        f"{provider.name}: {result}",
                    ))
                    continue

                candidates: list[AssetCandidate] = result
                for c in candidates:
                    if c.asset_id not in seen_ids:
                        seen_ids.add(c.asset_id)
                        all_candidates.append(c)

                provider_counts[provider.name] = provider_counts.get(provider.name, 0) + len(candidates)

            if all_candidates:
                break  # first successful query wins

        # Rank
        ranked = self._ranking.rank(
            all_candidates,
            query=primary_query,
            style=request.style,
        )

        return SearchResult(
            search_id=_make_search_id(request.query),
            normalized_query=primary_query,
            candidates=ranked[:request.limit],
            provider_results=provider_counts,
            errors=errors,
        )

    async def _search_provider(
        self, provider: AssetProvider, query: str, request: AssetSearchRequest
    ) -> list[AssetCandidate]:
        """Query a single provider with a specific query string."""
        provider_req = deepcopy(request)
        provider_req.query = query
        return await provider.search(provider_req)

"""Iconify SVG icon provider.

API docs: https://iconify.design/docs/api/
- Search: GET /search?query=...&limit=...
- SVG:    GET /{prefix}/{name}.svg
- Metadata: GET /{prefix}/{name}.json?icons=true

All requests go through a shared httpx.AsyncClient with connection
pooling and timeouts.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from assets.domain.models import AssetSearchRequest, AssetCandidate
from assets.providers.base import AssetProvider

logger = logging.getLogger(__name__)

ICONIFY_API_BASE = "https://api.iconify.design"
SEARCH_PATH = "/search"
DEFAULT_TIMEOUT_S = 10


class IconifyProvider(AssetProvider):
    """Provider that searches the Iconify SVG icon collection.

    Iconify aggregates 200k+ icons from 150+ icon sets (Material Design,
    FontAwesome, Tabler, etc.), all under permissive open-source licences.
    """

    def __init__(self, client: httpx.AsyncClient | None = None,
                 timeout: float = DEFAULT_TIMEOUT_S):
        self._client = client
        self._own_client = client is None
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "iconify"

    # ── public API ─────────────────────────────────────────────────────

    async def search(self, request: AssetSearchRequest) -> list[AssetCandidate]:
        client = await self._get_client()
        limit = min(request.limit, 20)

        try:
            resp = await client.get(
                ICONIFY_API_BASE + SEARCH_PATH,
                params={"query": request.query, "limit": limit},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            logger.warning("Iconify search timed out for query=%s", request.query)
            return []
        except httpx.HTTPStatusError as e:
            logger.warning("Iconify search HTTP %d for query=%s", e.response.status_code, request.query)
            return []
        except Exception as e:
            logger.warning("Iconify search failed for query=%s: %s", request.query, e)
            return []

        icons: list[dict] = data.get("icons") or data.get("collections") or []
        total = data.get("total", len(icons))

        if not icons:
            logger.info("Iconify returned no results for query=%s", request.query)
            return []

        # Iconify search returns icon identifiers like "mdi:emoticon-happy".
        results: list[AssetCandidate] = []
        for entry in icons[:limit]:
            icon_id: str = ""
            title: str = ""
            tags: list[str] = []

            if isinstance(entry, str):
                icon_id = entry
                title = entry.split(":")[-1].replace("-", " ").title()
            elif isinstance(entry, dict):
                icon_id = entry.get("id", entry.get("name", ""))
                title = entry.get("title", entry.get("name", icon_id.split(":")[-1]))
                tags = entry.get("tags", [])

            if not icon_id:
                continue

            provider_id = icon_id.replace(":", "-")
            prefix, name = icon_id.split(":", 1) if ":" in icon_id else ("", icon_id)

            results.append(AssetCandidate(
                asset_id=f"iconify:{icon_id}",
                provider=self.name,
                provider_asset_id=icon_id,
                title=title,
                tags=tags,
                format="svg",
                preview_url=f"{ICONIFY_API_BASE}/{prefix}/{name}.svg?height=128",
                download_url=f"{ICONIFY_API_BASE}/{prefix}/{name}.svg",
                source_url=f"https://icon-sets.iconify.design/{prefix}/{name}/",
                license_name="open-source (per-icon-set)",
            ))

        logger.info("Iconify search query=%s returned %d/%d results",
                     request.query, len(results), total)
        return results

    async def fetch(self, candidate: AssetCandidate) -> bytes:
        if not candidate.download_url:
            raise ValueError(f"No download_url for candidate {candidate.asset_id}")
        client = await self._get_client()
        try:
            resp = await client.get(candidate.download_url, timeout=self._timeout)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.warning("Iconify fetch failed for %s: %s", candidate.asset_id, e)
            raise

    async def fetch_metadata(self, candidate: AssetCandidate) -> dict:
        """Fetch icon metadata (tags, author, licence) from Iconify."""
        provider_id = candidate.provider_asset_id
        if ":" not in provider_id:
            return {}

        prefix, name = provider_id.split(":", 1)
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{ICONIFY_API_BASE}/{prefix}/{name}.json",
                params={"icons": "true"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "license_name": data.get("license", {}).get("name", ""),
                "author": data.get("author", {}).get("name", ""),
                "author_url": data.get("author", {}).get("url", ""),
                "tags": data.get("tags", []),
                "category": data.get("category", ""),
                "palette": data.get("palette", False),
            }
        except Exception:
            logger.debug("Iconify metadata fetch failed for %s", provider_id)
            return {}

    # ── internals ──────────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"User-Agent": "ai-draw/1.0"},
            )
            await self._client.__aenter__()
        return self._client

    async def close(self):
        if self._own_client and self._client is not None:
            await self._client.aclose()
            self._client = None

"""Local filesystem asset provider.

Phase 1: stub that returns empty results.
Phase 2+: reads from a local curated SVG library with pre-sanitized,
pre-indexed assets.
"""

from __future__ import annotations

import logging
from pathlib import Path

from assets.domain.models import AssetSearchRequest, AssetCandidate
from assets.providers.base import AssetProvider

logger = logging.getLogger(__name__)


class LocalAssetProvider(AssetProvider):
    """Provider for locally stored, pre-curated SVG assets.

    This provider is prioritised over remote providers because local
    assets are pre-sanitized, pre-indexed, and attribution-complete —
    they incur zero network latency and zero security risk.

    Storage layout (planned)::

        assets/storage/
            raw/          original downloaded files
            sanitized/    cleaned, safe SVG files
            previews/     rendered preview thumbnails
            metadata/     JSON index files
    """

    def __init__(self, storage_root: str | Path | None = None):
        self._storage_root = Path(storage_root) if storage_root else Path("assets/storage")

    @property
    def name(self) -> str:
        return "local"

    async def search(self, request: AssetSearchRequest) -> list[AssetCandidate]:
        """Phase 1 stub — always returns empty.

        In a future phase this will match *request.normalized_queries*
        against a local tag index and return pre-cached candidates.
        """
        logger.debug("LocalAssetProvider.search called (stub) — query=%s", request.query)
        return []

    async def fetch(self, candidate: AssetCandidate) -> bytes:
        raise NotImplementedError("LocalAssetProvider.fetch not yet implemented")

    async def fetch_metadata(self, candidate: AssetCandidate) -> dict:
        raise NotImplementedError("LocalAssetProvider.fetch_metadata not yet implemented")

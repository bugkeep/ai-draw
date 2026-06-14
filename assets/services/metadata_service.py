"""Asset metadata management — tracks source, license, author, and
content hashes for every cached asset.

Metadata schema stored in ``assets/storage/metadata/<cache_key>.json``::

    {
      "asset_id": "iconify:mdi:emoticon-happy",
      "provider": "iconify",
      "provider_asset_id": "mdi:emoticon-happy",
      "title": "Emoticon Happy",
      "format": "svg",
      "content_hash": "sha256:abc123...",
      "sanitizer_version": "1.0.0",
      "license": {
        "name": "Apache-2.0",
        "author": "Material Design Icons",
        "attribution_url": "https://..."
      },
      "files": {
        "raw": "raw/abc123...",
        "sanitized": "sanitized/abc123...svg",
        "preview": null
      },
      "cached_at": 1717000000.0
    }
"""

from __future__ import annotations

import time
from typing import Any

from assets.domain.models import AssetCandidate
from assets.services.cache_service import AssetCache
from assets.services.svg_sanitizer import SANITIZER_VERSION, content_hash


class MetadataService:
    """Read/write asset metadata, with convenience constructors from
    search results and download results."""

    def __init__(self, cache: AssetCache):
        self._cache = cache

    def build_metadata(self, candidate: AssetCandidate,
                       raw_data: bytes | None = None,
                       sanitized_data: bytes | None = None,
                       extra: dict | None = None) -> dict[str, Any]:
        """Construct a metadata dict for an asset.

        Args:
            candidate: The search result candidate.
            raw_data: Optional raw bytes (for content hash).
            sanitized_data: Optional sanitized bytes.
            extra: Optional extra fields to merge.

        Returns:
            A metadata dict ready for ``write_metadata``.
        """
        cache_key = self._cache.make_cache_key(candidate.provider, candidate.provider_asset_id)

        meta: dict[str, Any] = {
            "asset_id": candidate.asset_id,
            "provider": candidate.provider,
            "provider_asset_id": candidate.provider_asset_id,
            "title": candidate.title,
            "format": candidate.format,
            "sanitizer_version": SANITIZER_VERSION,
            "license": {
                "name": candidate.license_name or "",
                "author": candidate.author or "",
                "attribution_url": candidate.attribution_url or "",
            },
            "files": {
                "raw": None,
                "sanitized": None,
                "preview": None,
            },
            "cached_at": time.time(),
        }

        if raw_data is not None:
            meta["content_hash"] = content_hash(raw_data)
            meta["files"]["raw"] = f"raw/{cache_key}.raw"

        if sanitized_data is not None:
            meta["files"]["sanitized"] = f"sanitized/{cache_key}.svg"

        if extra:
            meta.update(extra)

        return meta

    def save(self, candidate: AssetCandidate, metadata: dict[str, Any]):
        """Write metadata to the cache."""
        cache_key = self._cache.make_cache_key(candidate.provider, candidate.provider_asset_id)
        self._cache.write_metadata(cache_key, metadata)

    def load(self, candidate: AssetCandidate) -> dict[str, Any]:
        """Load metadata from the cache."""
        cache_key = self._cache.make_cache_key(candidate.provider, candidate.provider_asset_id)
        return self._cache.read_metadata(cache_key)

    def exists(self, candidate: AssetCandidate) -> bool:
        """Check if metadata exists (implies the asset is cached)."""
        cache_key = self._cache.make_cache_key(candidate.provider, candidate.provider_asset_id)
        return self._cache.has_metadata(cache_key)

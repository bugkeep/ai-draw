"""Content-addressable cache for downloaded and sanitized assets.

Cache directory layout::

    assets/storage/
        raw/          original downloaded bytes (keyed by content hash)
        sanitized/    cleaned, safe bytes (keyed by content hash + sanitizer version)
        metadata/     JSON sidecar files
        previews/     (reserved for future thumbnails)

Cache key derivation::

    cache_key = SHA256(
        provider + ":" + provider_asset_id
        + ":" + sanitizer_version
    )
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from assets.domain.enums import AssetErrorCode
from assets.domain.errors import AssetCacheError
from assets.services.svg_sanitizer import SANITIZER_VERSION

logger = logging.getLogger(__name__)

# Default storage root relative to project root.
DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parent.parent.parent / "assets" / "storage"


class AssetCache:
    """Filesystem cache for asset raw data, sanitized data, and metadata.

    Thread-safe for reads.  Writes use atomic rename to avoid partial files.
    """

    def __init__(self, root: str | Path | None = None):
        self._root = Path(root) if root else DEFAULT_STORAGE_ROOT
        self._raw_dir = self._root / "raw"
        self._sanitized_dir = self._root / "sanitized"
        self._metadata_dir = self._root / "metadata"
        self._init_dirs()

    def _init_dirs(self):
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._sanitized_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_dir.mkdir(parents=True, exist_ok=True)

    # ── cache key ──────────────────────────────────────────────────────

    @staticmethod
    def make_cache_key(provider: str, provider_asset_id: str) -> str:
        """Derive a collision-resistant cache key.

        The key includes the sanitizer version so that upgrading the
        sanitizer invalidates old cached entries.
        """
        raw = f"{provider}:{provider_asset_id}:{SANITIZER_VERSION}"
        return hashlib.sha256(raw.encode()).hexdigest()

    # ── raw data ───────────────────────────────────────────────────────

    def raw_path(self, cache_key: str) -> Path:
        return self._raw_dir / f"{cache_key}.raw"

    def has_raw(self, cache_key: str) -> bool:
        return self.raw_path(cache_key).is_file()

    def write_raw(self, cache_key: str, data: bytes) -> Path:
        """Atomically write raw bytes."""
        dest = self.raw_path(cache_key)
        self._atomic_write(dest, data)
        logger.debug("Cached raw %s (%d bytes)", cache_key, len(data))
        return dest

    def read_raw(self, cache_key: str) -> bytes:
        path = self.raw_path(cache_key)
        if not path.is_file():
            raise AssetCacheError(f"raw cache miss: {cache_key}")
        return path.read_bytes()

    # ── sanitized data ─────────────────────────────────────────────────

    def sanitized_path(self, cache_key: str) -> Path:
        return self._sanitized_dir / f"{cache_key}.svg"

    def has_sanitized(self, cache_key: str) -> bool:
        return self.sanitized_path(cache_key).is_file()

    def write_sanitized(self, cache_key: str, data: bytes) -> Path:
        """Atomically write sanitized SVG bytes."""
        dest = self.sanitized_path(cache_key)
        self._atomic_write(dest, data)
        logger.debug("Cached sanitized %s (%d bytes)", cache_key, len(data))
        return dest

    def read_sanitized(self, cache_key: str) -> bytes:
        path = self.sanitized_path(cache_key)
        if not path.is_file():
            raise AssetCacheError(f"sanitized cache miss: {cache_key}")
        return path.read_bytes()

    # ── metadata ───────────────────────────────────────────────────────

    def metadata_path(self, cache_key: str) -> Path:
        return self._metadata_dir / f"{cache_key}.json"

    def has_metadata(self, cache_key: str) -> bool:
        return self.metadata_path(cache_key).is_file()

    def write_metadata(self, cache_key: str, meta: dict[str, Any]):
        """Atomically write metadata JSON."""
        path = self.metadata_path(cache_key)
        data = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
        self._atomic_write(path, data)

    def read_metadata(self, cache_key: str) -> dict[str, Any]:
        path = self.metadata_path(cache_key)
        if not path.is_file():
            raise AssetCacheError(f"metadata cache miss: {cache_key}")
        return json.loads(path.read_bytes())

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _atomic_write(dest: Path, data: bytes):
        """Write to a temp file and atomically replace the destination."""
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(str(tmp), str(dest))

    def purge(self, cache_key: str):
        """Remove all cached files for a key."""
        for path in [self.raw_path(cache_key), self.sanitized_path(cache_key),
                     self.metadata_path(cache_key)]:
            if path.is_file():
                os.remove(path)

    @property
    def root(self) -> Path:
        return self._root

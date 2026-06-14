"""Abstract base class for visual asset providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from assets.domain.models import AssetSearchRequest, AssetCandidate


class AssetProvider(ABC):
    """Interface for searching and fetching visual assets.

    Each provider wraps one external or local source of SVG/raster assets.
    Providers are registered in the SearchService and queried in parallel.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier, e.g. ``"iconify"``, ``"local"``."""
        ...

    @abstractmethod
    async def search(self, request: AssetSearchRequest) -> list[AssetCandidate]:
        """Search the provider for assets matching *request*.

        Returns a list of candidates; may be empty.  The provider should
        fill in as many fields of each candidate as possible (title, tags,
        preview_url, download_url, width, height, license, author, etc.).
        """
        ...

    @abstractmethod
    async def fetch(self, candidate: AssetCandidate) -> bytes:
        """Download the raw asset bytes for *candidate*.

        The caller is responsible for sanitization and caching.
        """
        ...

    @abstractmethod
    async def fetch_metadata(self, candidate: AssetCandidate) -> dict:
        """Fetch extended metadata for *candidate* (licence, author, tags).

        Returns a dict with standardised keys (license_name, author,
        attribution_url, tags, etc.).
        """
        ...

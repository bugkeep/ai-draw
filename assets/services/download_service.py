"""Secure download service — chains URL validation, DNS/IP checks, MIME
validation, SVG sanitization, and caching.

Flow::

    URL → validate URL → resolve DNS → check IP → HTTP GET (with redirect limit)
        → check MIME → check size → (SVG) sanitize → cache → return content
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import httpx

from assets.domain.errors import AssetDownloadError
from assets.domain.enums import AssetErrorCode
from server.security.url_validator import validate_url, validate_redirect, URLValidationError
from server.security.network_policy import resolve_and_check, NetworkPolicyError
from server.security.mime_validator import validate_mime, validate_size, MIMEValidationError
from assets.services.svg_sanitizer import SvgSanitizer

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 15
MAX_REDIRECTS = 3
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10 MB before any processing


class DownloadResult:
    """Result of a secure download."""

    def __init__(self, data: bytes, content_type: str,
                 source_url: str, final_url: str,
                 sanitized: bool = False):
        self.data = data
        self.content_type = content_type
        self.source_url = source_url
        self.final_url = final_url
        self.sanitized = sanitized


class DownloadService:
    """Secure asset downloader with full security pipeline."""

    def __init__(self, client: httpx.AsyncClient | None = None,
                 allowed_domains: set[str] | None = None,
                 svg_sanitizer: SvgSanitizer | None = None,
                 on_progress: Callable | None = None,
                 timeout: float = DEFAULT_TIMEOUT_S):
        self._client = client
        self._own_client = client is None
        self._allowed_domains = allowed_domains
        self._sanitizer = svg_sanitizer or SvgSanitizer()
        self._timeout = timeout

    async def download(self, url: str, fmt: str = "svg") -> DownloadResult:
        """Securely download an asset through the full safety pipeline.

        Args:
            url: Source URL (must be validatable).
            fmt: Expected format (svg, png, jpg, etc.).

        Returns:
            DownloadResult with cleaned data.

        Raises:
            AssetDownloadError: At any point in the pipeline.
        """
        # 1. URL validation
        try:
            validated_url = validate_url(url, self._allowed_domains)
        except URLValidationError as e:
            raise AssetDownloadError(AssetErrorCode.UNSAFE_URL, str(e)) from e

        # 2. DNS/IP check
        parsed = __import__("urllib.parse").urlparse(validated_url)
        hostname = parsed.hostname or ""
        try:
            resolve_and_check(hostname)
        except NetworkPolicyError as e:
            raise AssetDownloadError(AssetErrorCode.UNSAFE_URL, str(e)) from e

        # 3. HTTP download with redirect tracking
        client = await self._get_client()
        current_url = validated_url
        redirect_count = 0
        response = None

        try:
            while True:
                resp = await client.get(
                    current_url,
                    follow_redirects=False,
                    timeout=self._timeout,
                )
                # Check for redirect
                if resp.status_code in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    if redirect_count > MAX_REDIRECTS:
                        raise AssetDownloadError(
                            AssetErrorCode.DOWNLOAD_FAILED,
                            f"too many redirects ({redirect_count})",
                        )
                    location = resp.headers.get("location", "")
                    if not location:
                        raise AssetDownloadError(
                            AssetErrorCode.DOWNLOAD_FAILED,
                            "redirect with no Location header",
                        )
                    # Validate redirect target
                    try:
                        current_url = validate_redirect(validated_url, location,
                                                        self._allowed_domains)
                    except URLValidationError as e:
                        raise AssetDownloadError(AssetErrorCode.UNSAFE_URL, str(e)) from e
                    # DNS check for redirect target
                    redirect_host = __import__("urllib.parse").urlparse(current_url).hostname or ""
                    try:
                        resolve_and_check(redirect_host)
                    except NetworkPolicyError as e:
                        raise AssetDownloadError(AssetErrorCode.UNSAFE_URL, str(e)) from e
                    continue

                response = resp
                break

            if response is None:
                raise AssetDownloadError(AssetErrorCode.DOWNLOAD_FAILED, "no response")

            response.raise_for_status()

        except httpx.TimeoutException as e:
            raise AssetDownloadError(AssetErrorCode.DOWNLOAD_FAILED, f"timeout: {e}") from e
        except httpx.HTTPError as e:
            raise AssetDownloadError(AssetErrorCode.DOWNLOAD_FAILED, f"HTTP error: {e}") from e

        # 4. MIME validation
        content_type = response.headers.get("content-type", "")
        try:
            validate_mime(content_type, fmt)
        except MIMEValidationError as e:
            raise AssetDownloadError(AssetErrorCode.INVALID_MIME, str(e)) from e

        # 5. Size check
        content_length = len(response.content)
        try:
            validate_size(content_length, fmt)
        except MIMEValidationError as e:
            raise AssetDownloadError(AssetErrorCode.DOWNLOAD_FAILED, str(e)) from e

        data = response.content

        # 6. SVG sanitization
        sanitized = False
        if fmt == "svg":
            try:
                data = self._sanitizer.sanitize(data)
                sanitized = True
            except Exception as e:
                raise AssetDownloadError(AssetErrorCode.SVG_SANITIZE_FAILED, str(e)) from e

        return DownloadResult(
            data=data,
            content_type=content_type,
            source_url=url,
            final_url=current_url,
            sanitized=sanitized,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"User-Agent": "ai-draw/1.0"},
                max_redirects=0,  # we handle redirects manually
            )
            await self._client.__aenter__()
        return self._client

    async def close(self):
        if self._own_client and self._client is not None:
            await self._client.aclose()
            self._client = None

"""URL safety validation for asset downloads.

Enforces protocol, host, and redirect policies defined in the
asset security architecture.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Schemes that are never allowed for asset downloads.
FORBIDDEN_SCHEMES = {"file", "ftp", "gopher", "data", "javascript", "blob"}

# Only HTTPS is allowed for remote downloads.
ALLOWED_SCHEMES = {"https"}

# Provider domain whitelist (first-party sources).
PROVIDER_DOMAINS: dict[str, set[str]] = {
    "iconify": {
        "api.iconify.design",
        "icon-sets.iconify.design",
    },
}

DEFAULT_ALLOWED_DOMAINS: set[str] = set()
for domains in PROVIDER_DOMAINS.values():
    DEFAULT_ALLOWED_DOMAINS.update(domains)


class URLValidationError(ValueError):
    """Raised when a URL fails security validation."""

    def __init__(self, url: str, reason: str):
        self.url = url
        super().__init__(f"URL rejected: {reason} (url={url})")


def validate_url(url: str, allowed_domains: set[str] | None = None) -> str:
    """Validate a URL for safe asset downloading.

    Args:
        url: The URL to validate.
        allowed_domains: Optional set of allowed hostnames.  Defaults to
                         the built-in provider whitelist.

    Returns:
        The validated URL (unchanged).

    Raises:
        URLValidationError: If the URL fails any check.
    """
    if not url or not url.strip():
        raise URLValidationError(url, "empty URL")

    url = url.strip()

    # Parse
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise URLValidationError(url, f"parse error: {e}") from e

    # Scheme check
    scheme = (parsed.scheme or "").lower()
    if scheme in FORBIDDEN_SCHEMES:
        raise URLValidationError(url, f"forbidden scheme: {scheme}")
    if scheme not in ALLOWED_SCHEMES:
        raise URLValidationError(url, f"only HTTPS allowed, got: {scheme}")

    # Host check
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise URLValidationError(url, "no hostname")

    allowed = allowed_domains if allowed_domains is not None else DEFAULT_ALLOWED_DOMAINS
    if allowed and hostname not in allowed:
        raise URLValidationError(url, f"host not in whitelist: {hostname}")

    return url


def validate_redirect(original_url: str, redirect_url: str,
                      allowed_domains: set[str] | None = None) -> str:
    """Validate a redirect target after an initial URL was already approved.

    The redirect target must pass the same validation as the original URL.
    """
    if not redirect_url or redirect_url == original_url:
        return original_url
    return validate_url(redirect_url, allowed_domains)


def is_safe_redirect(original: str, target: str) -> bool:
    """Quick boolean check: is the redirect safe to follow?"""
    try:
        validate_redirect(original, target)
        return True
    except URLValidationError:
        return False

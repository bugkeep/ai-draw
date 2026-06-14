"""MIME type validation for downloaded assets.

Ensures that downloaded content matches the expected format and rejects
mismatched or potentially dangerous content types.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Allowed MIME types by asset format.
ALLOWED_MIME_TYPES: dict[str, set[str]] = {
    "svg": {
        "image/svg+xml",
        "text/plain",           # some servers send text/plain for SVG
        "application/xml",      # some send application/xml
        "text/xml",             # or text/xml
    },
    "png": {"image/png"},
    "jpg": {"image/jpeg", "image/jpg"},
    "jpeg": {"image/jpeg", "image/jpg"},
    "webp": {"image/webp"},
    "gif": {"image/gif"},
}

# Size limits per format.
MAX_CONTENT_LENGTH: dict[str, int] = {
    "svg": 1 * 1024 * 1024,        # 1 MB
    "png": 10 * 1024 * 1024,       # 10 MB
    "jpg": 10 * 1024 * 1024,       # 10 MB
    "jpeg": 10 * 1024 * 1024,      # 10 MB
    "webp": 10 * 1024 * 1024,      # 10 MB
    "gif": 10 * 1024 * 1024,       # 10 MB
}

DEFAULT_MAX_LENGTH = 1 * 1024 * 1024


class MIMEValidationError(ValueError):
    """Raised when content fails MIME validation."""

    def __init__(self, reason: str, content_type: str = "", fmt: str = ""):
        self.content_type = content_type
        self.fmt = fmt
        super().__init__(f"MIME validation failed: {reason}")


def validate_mime(content_type: str, fmt: str) -> str:
    """Check that *content_type* is acceptable for format *fmt*.

    Returns:
        The validated content type.

    Raises:
        MIMEValidationError: If the content type is not allowed.
    """
    norm_ct = (content_type or "").lower().split(";")[0].strip()

    allowed = ALLOWED_MIME_TYPES.get(fmt, set())
    if not allowed:
        logger.debug("No MIME restrictions for format=%s, allowing %s", fmt, norm_ct)
        return norm_ct

    if norm_ct not in allowed:
        raise MIMEValidationError(
            f"unexpected content type '{norm_ct}' for format '{fmt}'",
            content_type=norm_ct,
            fmt=fmt,
        )

    return norm_ct


def validate_size(content_length: int | None, fmt: str) -> int:
    """Check that *content_length* is within limits for *fmt*.

    Returns:
        The validated content length.

    Raises:
        MIMEValidationError: If the content is too large.
    """
    max_len = MAX_CONTENT_LENGTH.get(fmt, DEFAULT_MAX_LENGTH)

    if content_length is not None and content_length > max_len:
        raise MIMEValidationError(
            f"content too large: {content_length} > {max_len} bytes",
            fmt=fmt,
        )

    return content_length or 0

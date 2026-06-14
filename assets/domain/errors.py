from .enums import AssetErrorCode


class AssetError(Exception):
    """Base exception for all asset pipeline errors."""

    def __init__(self, code: AssetErrorCode, message: str, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(f"[{code.value}] {message}")


class AssetProviderError(AssetError):
    """Provider-level error (unavailable, timeout, auth failure)."""

    def __init__(self, message: str, provider: str = "", details: dict | None = None):
        super().__init__(AssetErrorCode.PROVIDER_UNAVAILABLE, message, details)
        self.provider = provider


class AssetDownloadError(AssetError):
    """Download-level error (network, MIME, URL safety)."""

    def __init__(self, code: AssetErrorCode, message: str, details: dict | None = None):
        super().__init__(code, message, details)


class AssetSanitizeError(AssetError):
    """SVG sanitization error."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(AssetErrorCode.SVG_SANITIZE_FAILED, message, details)


class AssetCacheError(AssetError):
    """Cache-level error (write, read, integrity)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(AssetErrorCode.CACHE_FAILED, message, details)


class AssetImportError(AssetError):
    """Import-level error (Fabric.js processing)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(AssetErrorCode.IMPORT_FAILED, message, details)

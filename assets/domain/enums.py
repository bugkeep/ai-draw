from enum import Enum


class DrawingMode(str, Enum):
    """Intent classification for a user's drawing request."""

    PRIMITIVE = "primitive"
    DIAGRAM = "diagram"
    VECTOR_ASSET = "vector_asset"
    RASTER_ASSET = "raster_asset"
    IMAGE_GENERATION = "image_generation"
    CANVAS_EDIT = "canvas_edit"


class AssetType(str, Enum):
    """Type of visual asset being searched or imported."""

    VECTOR = "vector"
    RASTER = "raster"


class AssetErrorCode(str, Enum):
    """Categorised error codes for the asset pipeline."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NO_RESULTS = "no_results"
    LOW_RELEVANCE = "low_relevance"
    DOWNLOAD_FAILED = "download_failed"
    INVALID_MIME = "invalid_mime"
    UNSAFE_URL = "unsafe_url"
    SVG_PARSE_FAILED = "svg_parse_failed"
    SVG_SANITIZE_FAILED = "svg_sanitize_failed"
    CACHE_FAILED = "cache_failed"
    IMPORT_FAILED = "import_failed"

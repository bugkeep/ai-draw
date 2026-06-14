from pydantic import BaseModel, Field
from .enums import DrawingMode


class DrawingRoute(BaseModel):
    """Result of classifying a user's drawing request into a mode."""

    mode: DrawingMode
    confidence: float = Field(ge=0.0, le=1.0)
    subject: str
    style: str | None = None
    reason: str
    requires_search: bool = False
    requires_existing_object: bool = False


class AssetSearchRequest(BaseModel):
    """Normalised request for searching visual assets."""

    query: str
    normalized_queries: list[str] = []
    asset_type: str = "vector"
    style: str = "any"
    limit: int = Field(default=8, ge=1, le=20)
    preferred_sources: list[str] = []
    required_license_types: list[str] = []
    transparent_background: bool | None = None


class AssetCandidate(BaseModel):
    """A single candidate returned by an asset provider."""

    asset_id: str
    provider: str
    provider_asset_id: str

    title: str
    description: str | None = None
    tags: list[str] = []

    format: str
    preview_url: str | None = None
    source_url: str | None = None
    download_url: str | None = None

    width: int | None = None
    height: int | None = None

    style: str | None = None
    license_name: str | None = None
    author: str | None = None
    attribution_url: str | None = None

    text_score: float = 0.0
    style_score: float = 0.0
    quality_score: float = 0.0
    editability_score: float = 0.0
    final_score: float = 0.0


class CanvasAssetObject(BaseModel):
    """Represents an imported asset on the Fabric.js canvas."""

    object_id: str
    semantic_type: str
    asset_id: str

    left: float
    top: float
    width: float
    height: float
    angle: float = 0.0
    opacity: float = 1.0

    source: str
    license_name: str | None = None
    author: str | None = None
    attribution_url: str | None = None

    import_mode: str = "svg_group"
    editable: bool = True

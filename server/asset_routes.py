"""Asset-serving HTTP endpoints.

These routes serve cached, sanitized assets to the frontend.  They never
expose raw download URLs or external URLs directly.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

router = APIRouter(prefix="/assets", tags=["assets"])

# Storage root — assets/storage/ relative to project root.
_STORAGE_ROOT = Path(__file__).resolve().parent.parent.parent / "assets" / "storage"


@router.get("/content/{cache_key}.svg")
async def serve_sanitized_svg(cache_key: str):
    """Serve a cached, sanitized SVG by its cache key.

    The cache key is a SHA-256 hex digest (64 characters).
    """
    svg_path = _STORAGE_ROOT / "sanitized" / f"{cache_key}.svg"
    if not svg_path.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(
        str(svg_path),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/preview/{cache_key}")
async def serve_preview(cache_key: str):
    """Serve a preview image for an asset.

    Phase 1: redirects to the sanitized SVG (clients render it).
    Phase 2: may serve a resampled thumbnail.
    """
    svg_path = _STORAGE_ROOT / "sanitized" / f"{cache_key}.svg"
    if not svg_path.is_file():
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(
        str(svg_path),
        media_type="image/svg+xml",
    )


@router.get("/metadata/{cache_key}")
async def serve_metadata(cache_key: str):
    """Return JSON metadata for a cached asset."""
    meta_path = _STORAGE_ROOT / "metadata" / f"{cache_key}.json"
    if not meta_path.is_file():
        raise HTTPException(status_code=404, detail="Metadata not found")
    return Response(
        content=meta_path.read_bytes(),
        media_type="application/json",
    )

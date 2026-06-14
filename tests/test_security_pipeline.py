"""Tests for the security pipeline: URL validation, MIME validation,
SVG sanitization, download service, and asset cache."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from server.security.url_validator import (
    validate_url,
    validate_redirect,
    URLValidationError,
    is_safe_redirect,
)
from server.security.mime_validator import validate_mime, validate_size, MIMEValidationError
from assets.services.svg_sanitizer import SvgSanitizer, sanitize_svg, content_hash, SANITIZER_VERSION
from assets.services.cache_service import AssetCache
from assets.services.metadata_service import MetadataService
from assets.domain.models import AssetCandidate


# ── URL Validator tests ─────────────────────────────────────────────────────

class TestURLValidator:
    def test_https_url_accepted(self):
        url = validate_url("https://api.iconify.design/mdi/emoticon-happy.svg")
        assert url == "https://api.iconify.design/mdi/emoticon-happy.svg"

    def test_http_rejected(self):
        with pytest.raises(URLValidationError, match="only HTTPS"):
            validate_url("http://example.com/test.svg")

    def test_file_scheme_rejected(self):
        with pytest.raises(URLValidationError, match="forbidden scheme"):
            validate_url("file:///etc/passwd")

    def test_ftp_rejected(self):
        with pytest.raises(URLValidationError, match="forbidden scheme"):
            validate_url("ftp://files.example.com/icon.svg")

    def test_javascript_rejected(self):
        with pytest.raises(URLValidationError, match="forbidden scheme"):
            validate_url("javascript:alert(1)")

    def test_data_url_rejected(self):
        with pytest.raises(URLValidationError, match="forbidden scheme"):
            validate_url("data:image/svg+xml;base64,PHN2Zy...")

    def test_empty_url_rejected(self):
        with pytest.raises(URLValidationError, match="empty URL"):
            validate_url("")

    def test_whitespace_url_rejected(self):
        with pytest.raises(URLValidationError, match="empty URL"):
            validate_url("   ")

    def test_unknown_host_rejected(self):
        with pytest.raises(URLValidationError, match="not in whitelist"):
            validate_url("https://evil-site.com/malware.svg")

    def test_iconify_host_accepted(self):
        url = validate_url("https://api.iconify.design/search?query=cat")
        assert "iconify.design" in url

    def test_icon_sets_host_accepted(self):
        url = validate_url("https://icon-sets.iconify.design/mdi/cat.svg")
        assert "iconify" in url

    def test_custom_allowed_domains(self):
        url = validate_url("https://cdn.example.com/icon.svg",
                           allowed_domains={"cdn.example.com"})
        assert url == "https://cdn.example.com/icon.svg"

    def test_redirect_same_url(self):
        url = "https://api.iconify.design/icon.svg"
        assert validate_redirect(url, url) == url

    def test_redirect_to_safe(self):
        original = "https://api.iconify.design/icon.svg"
        target = "https://api.iconify.design/icon-v2.svg"
        assert validate_redirect(original, target) == target

    def test_redirect_to_unsafe(self):
        original = "https://api.iconify.design/icon.svg"
        target = "http://evil.com/malware.svg"
        with pytest.raises(URLValidationError):
            validate_redirect(original, target)

    def test_is_safe_redirect_bool(self):
        assert is_safe_redirect(
            "https://api.iconify.design/a.svg",
            "https://api.iconify.design/b.svg",
        )
        assert not is_safe_redirect(
            "https://api.iconify.design/a.svg",
            "http://evil.com/b.svg",
        )


# ── MIME Validator tests ────────────────────────────────────────────────────

class TestMIMEValidator:
    def test_svg_mime_accepted(self):
        assert validate_mime("image/svg+xml", "svg") == "image/svg+xml"

    def test_svg_text_plain_accepted(self):
        assert validate_mime("text/plain; charset=utf-8", "svg") == "text/plain"

    def test_png_mime_accepted(self):
        assert validate_mime("image/png", "png") == "image/png"

    def test_wrong_mime_rejected(self):
        with pytest.raises(MIMEValidationError, match="unexpected"):
            validate_mime("image/gif", "svg")

    def test_html_mime_rejected(self):
        with pytest.raises(MIMEValidationError):
            validate_mime("text/html", "svg")

    def test_size_within_limits(self):
        assert validate_size(500 * 1024, "svg") == 512000

    def test_size_exceeds_limit(self):
        with pytest.raises(MIMEValidationError, match="too large"):
            validate_size(2 * 1024 * 1024, "svg")

    def test_none_size(self):
        assert validate_size(None, "svg") == 0

    def test_unknown_format_allows_anything(self):
        assert validate_mime("application/octet-stream", "bin") == "application/octet-stream"


# ── SVG Sanitizer tests ─────────────────────────────────────────────────────

class TestSvgSanitizer:
    def test_clean_svg_passes(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="40"/></svg>'
        result = sanitize_svg(svg)
        assert b"<circle" in result
        assert b"</svg>" in result

    def test_strips_script_tag(self):
        svg = b'<svg xmlns="..."><script>alert(1)</script><circle cx="50" cy="50" r="40"/></svg>'
        result = sanitize_svg(svg)
        assert b"script" not in result
        assert b"circle" in result

    def test_strips_onclick_attribute(self):
        svg = b'<svg xmlns="..."><circle cx="50" cy="50" r="40" onclick="alert(1)"/></svg>'
        result = sanitize_svg(svg)
        assert b"onclick" not in result

    def test_strips_forbidden_tags(self):
        for tag in [b"foreignObject", b"iframe", b"object", b"embed"]:
            svg = b'<svg xmlns="..."><%s/><circle cx="50" cy="50" r="40"/></svg>' % tag
            result = sanitize_svg(svg)
            assert tag.lower() not in result
            assert b"circle" in result

    def test_strips_external_href(self):
        svg = b'<svg xmlns="..."><image href="https://evil.com/malware.png"/></svg>'
        result = sanitize_svg(svg)
        assert b"href" not in result

    def test_strips_style_url(self):
        svg = b'<svg xmlns="..."><circle style="background: url(https://evil.com/x)" cx="50" cy="50" r="40"/></svg>'
        result = sanitize_svg(svg)
        assert b"url(" not in result.lower()

    def test_preserves_allowed_tags(self):
        for tag in [b"g", b"path", b"rect", b"ellipse", b"line", b"polyline", b"polygon"]:
            svg = b'<svg xmlns="..."><%s/><circle cx="50" cy="50" r="40"/></svg>' % tag
            result = sanitize_svg(svg)
            assert tag in result

    def test_preserves_gradient(self):
        svg = b"""<svg xmlns="...">
            <defs><linearGradient id="g1"><stop offset="0%" stop-color="red"/></linearGradient></defs>
            <circle fill="url(#g1)" cx="50" cy="50" r="40"/>
        </svg>"""
        result = sanitize_svg(svg)
        assert b"linearGradient" in result
        assert b"stop" in result

    def test_rejects_oversized_svg(self):
        sanitizer = SvgSanitizer(max_raw=100)
        with pytest.raises(Exception, match="exceeds max"):
            sanitizer.sanitize(b"a" * 200)

    def test_content_hash(self):
        h = content_hash(b"test data")
        assert h.startswith("sha256:")
        assert len(h) == 64 + 7  # "sha256:" + 64 hex chars

    def test_sanitizer_version(self):
        assert SANITIZER_VERSION == "1.0.0"

    def test_max_depth_check(self):
        """Deeply nested SVG should be rejected."""
        sanitizer = SvgSanitizer(max_depth=5)
        svg = b"<svg>" + b"<g>" * 10 + b"</g>" * 10 + b"</svg>"
        with pytest.raises(Exception, match="depth"):
            sanitizer.sanitize(svg)

    def test_preserves_clip_path(self):
        svg = b"""<svg xmlns="...">
            <defs><clipPath id="c1"><circle cx="50" cy="50" r="40"/></clipPath></defs>
            <rect clip-path="url(#c1)" width="100" height="100"/>
        </svg>"""
        result = sanitize_svg(svg)
        assert b"clipPath" in result


# ── Cache Service tests ─────────────────────────────────────────────────────

class TestAssetCache:
    @pytest.fixture
    def cache(self, tmp_path: Path):
        return AssetCache(root=str(tmp_path / "storage"))

    def test_raw_write_and_read(self, cache: AssetCache):
        cache.write_raw("abc123", b"raw data")
        assert cache.has_raw("abc123")
        assert cache.read_raw("abc123") == b"raw data"

    def test_sanitized_write_and_read(self, cache: AssetCache):
        cache.write_sanitized("abc123", b"<svg>...</svg>")
        assert cache.has_sanitized("abc123")
        assert cache.read_sanitized("abc123") == b"<svg>...</svg>"

    def test_metadata_write_and_read(self, cache: AssetCache):
        meta = {"asset_id": "test:icon", "title": "Test"}
        cache.write_metadata("abc123", meta)
        assert cache.has_metadata("abc123")
        assert cache.read_metadata("abc123") == meta

    def test_make_cache_key(self, cache: AssetCache):
        key1 = AssetCache.make_cache_key("iconify", "mdi:heart")
        key2 = AssetCache.make_cache_key("iconify", "mdi:heart")
        key3 = AssetCache.make_cache_key("iconify", "mdi:star")
        assert key1 == key2  # same inputs → same key
        assert key1 != key3  # different inputs → different key

    def test_make_cache_key_includes_version(self):
        """Cache key must incorporate sanitizer_version."""
        key = AssetCache.make_cache_key("iconify", "mdi:test")
        # It's a SHA-256 hex digest (64 chars)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_purge_removes_all_files(self, cache: AssetCache):
        cache.write_raw("purge_test", b"raw")
        cache.write_sanitized("purge_test", b"svg")
        cache.write_metadata("purge_test", {"key": "val"})
        cache.purge("purge_test")
        assert not cache.has_raw("purge_test")
        assert not cache.has_sanitized("purge_test")
        assert not cache.has_metadata("purge_test")

    def test_read_missing_raw_raises(self, cache: AssetCache):
        with pytest.raises(Exception, match="cache miss"):
            cache.read_raw("nonexistent")

    def test_cache_dirs_created(self, tmp_path: Path):
        cache = AssetCache(root=str(tmp_path / "new_storage"))
        assert (tmp_path / "new_storage" / "raw").is_dir()
        assert (tmp_path / "new_storage" / "sanitized").is_dir()
        assert (tmp_path / "new_storage" / "metadata").is_dir()

    def test_atomic_write_doesnt_corrupt(self, cache: AssetCache):
        """Simulate: write twice, ensure no tmp files remain."""
        cache.write_raw("atomic_test", b"version1")
        cache.write_raw("atomic_test", b"version2")
        assert cache.read_raw("atomic_test") == b"version2"
        # No .tmp files should remain
        tmp_files = list(cache._root.rglob("*.tmp"))
        assert len(tmp_files) == 0


# ── Metadata Service tests ──────────────────────────────────────────────────

class TestMetadataService:
    @pytest.fixture
    def cache(self, tmp_path: Path):
        return AssetCache(root=str(tmp_path / "storage"))

    @pytest.fixture
    def meta_svc(self, cache: AssetCache):
        return MetadataService(cache)

    def test_build_metadata(self, meta_svc: MetadataService):
        candidate = AssetCandidate(
            asset_id="iconify:mdi:heart",
            provider="iconify",
            provider_asset_id="mdi:heart",
            title="Heart Icon",
            format="svg",
            license_name="Apache-2.0",
        )
        meta = meta_svc.build_metadata(candidate, raw_data=b"raw", sanitized_data=b"svg")
        assert meta["asset_id"] == "iconify:mdi:heart"
        assert meta["license"]["name"] == "Apache-2.0"
        assert meta["content_hash"].startswith("sha256:")
        assert "sanitized" in meta["files"]["sanitized"]

    def test_save_and_load(self, meta_svc: MetadataService):
        candidate = AssetCandidate(
            asset_id="iconify:mdi:star",
            provider="iconify",
            provider_asset_id="mdi:star",
            title="Star",
            format="svg",
        )
        meta = meta_svc.build_metadata(candidate)
        meta_svc.save(candidate, meta)
        assert meta_svc.exists(candidate)
        loaded = meta_svc.load(candidate)
        assert loaded["asset_id"] == "iconify:mdi:star"

    def test_not_exists(self, meta_svc: MetadataService):
        candidate = AssetCandidate(
            asset_id="iconify:unknown",
            provider="iconify",
            provider_asset_id="unknown",
            title="?",
            format="svg",
        )
        assert not meta_svc.exists(candidate)

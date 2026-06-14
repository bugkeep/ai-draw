"""SVG sanitization — strip dangerous nodes, attributes, and references.

P0 security module.  Every externally-sourced SVG must pass through
this sanitizer before being cached or served to the frontend.
"""

from __future__ import annotations

import logging
import re
import hashlib
from xml.etree.ElementTree import (
    Element,  # noqa: F401 (re-exported for type annotation convenience)
)
from xml.etree import ElementTree as ET
from io import BytesIO

from assets.domain.errors import AssetSanitizeError

logger = logging.getLogger(__name__)

SANITIZER_VERSION = "1.0.0"

# These tags are STRICTLY FORBIDDEN (removed with their entire subtree).
FORBIDDEN_TAGS: set[str] = {
    "script",
    "foreignobject",
    "foreignObject",
    "iframe",
    "object",
    "embed",
    "audio",
    "video",
    "use",              # may reference external resources
}

# Tags that are ALWAYS ALLOWED.
ALLOWED_TAGS: set[str] = {
    "svg",
    "g",
    "path",
    "circle",
    "ellipse",
    "rect",
    "line",
    "polyline",
    "polygon",
    "defs",
    "lineargradient",
    "radialgradient",
    "stop",
    "clippath",
    "mask",
    "style",            # limited support (inline only, no external references)
}

# Additional tags permitted when they appear inside a well-known parent
# (e.g., <linearGradient> children).  These are NOT standalone-safe.
_ALLOWED_INSIDE_DEFS: set[str] = {
    "linearGradient",
    "radialGradient",
    "stop",
    "clipPath",
    "mask",
    "filter",
    "feGaussianBlur",
    "feOffset",
    "feMerge",
    "feMergeNode",
    "feColorMatrix",
    "feComposite",
    "feBlend",
    "feFlood",
    "feTile",
}

# Combined set for quick membership lookup.
_ALLOWED_ALL = ALLOWED_TAGS | _ALLOWED_INSIDE_DEFS

# Attributes that are ALWAYS FORBIDDEN (removed).
FORBIDDEN_ATTR_PATTERNS: list[re.Pattern] = [
    re.compile(r"^on\w+$", re.IGNORECASE),        # onload, onclick, etc.
]

# XLink namespace.
XLINK_NS = "http://www.w3.org/1999/xlink"

# Resource limits.
MAX_RAW_BYTES = 1 * 1024 * 1024      # 1 MB
MAX_SANITIZED_BYTES = 1 * 1024 * 1024
MAX_XML_NODES = 5000
MAX_XML_DEPTH = 50


class SvgSanitizer:
    """Safe SVG processor.

    Usage::

        sanitizer = SvgSanitizer()
        cleaned = sanitizer.sanitize(raw_svg_bytes)
        # cleaned is safe SVG bytes, or None if the SVG was entirely removed.
    """

    def __init__(self, max_raw: int = MAX_RAW_BYTES,
                 max_nodes: int = MAX_XML_NODES,
                 max_depth: int = MAX_XML_DEPTH):
        self.max_raw = max_raw
        self.max_nodes = max_nodes
        self.max_depth = max_depth

    @property
    def version(self) -> str:
        return SANITIZER_VERSION

    def sanitize(self, raw: bytes) -> bytes:
        """Parse, clean, and re-serialise an SVG.

        Returns:
            Cleaned SVG bytes.  May be empty (``b""``) if all content
            was stripped.

        Raises:
            AssetSanitizeError: If parsing fails or resource limits
                are exceeded.
        """
        # Size check
        if len(raw) > self.max_raw:
            raise AssetSanitizeError(
                f"SVG exceeds max raw size ({len(raw)} > {self.max_raw})",
            )

        # Parse
        try:
            tree = ET.parse(BytesIO(raw))
            root = tree.getroot()
        except ET.ParseError as e:
            raise AssetSanitizeError(f"XML parse error: {e}") from e

        # Normalise namespace
        self._normalise_ns(root)

        # Depth check
        depth = self._max_depth(root)
        if depth > self.max_depth:
            raise AssetSanitizeError(f"SVG depth {depth} exceeds limit {self.max_depth}")

        # Clean
        self._clean_node(root, depth=0)

        # Node count check
        node_count = self._count_nodes(root)
        if node_count > self.max_nodes:
            raise AssetSanitizeError(f"SVG node count {node_count} exceeds limit {self.max_nodes}")

        # Serialize
        try:
            cleaned = ET.tostring(root, encoding="utf-8")
        except Exception as e:
            raise AssetSanitizeError(f"serialization failed: {e}") from e

        if len(cleaned) > MAX_SANITIZED_BYTES:
            raise AssetSanitizeError(
                f"cleaned SVG too large ({len(cleaned)} > {MAX_SANITIZED_BYTES})",
            )

        return cleaned

    # ── tree helpers ───────────────────────────────────────────────────

    @staticmethod
    def _normalise_ns(el: Element):
        """Strip namespace URIs so tag names are comparable."""
        # Rewrite the tag
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        for child in el:
            SvgSanitizer._normalise_ns(child)

    def _clean_node(self, el: Element, depth: int):
        """Recursively remove forbidden nodes and attributes."""
        if depth > self.max_depth:
            return

        # Remove forbidden attributes
        attrs_to_del: list[str] = []
        for attr_name in el.attrib:
            stripped = attr_name.split("}")[-1] if "}" in attr_name else attr_name
            if self._is_attr_forbidden(attr_name, stripped, el.attrib[attr_name]):
                attrs_to_del.append(attr_name)

        for attr_name in attrs_to_del:
            del el.attrib[attr_name]

        # Remove xlink namespace
        if XLINK_NS in el.attrib.get("xmlns", ""):
            del el.attrib["xmlns"]  # will be re-mapped by tostring

        # Recursively clean children
        children_to_keep: list[Element] = []
        for child in el:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if child_tag in FORBIDDEN_TAGS:
                continue  # remove entire subtree

            if child_tag not in _ALLOWED_ALL:
                continue  # unknown tag → remove

            self._clean_node(child, depth + 1)
            children_to_keep.append(child)

        # Replace children
        el[:] = children_to_keep

    def _is_attr_forbidden(self, full_name: str, stripped: str, value: str) -> bool:
        """Check if an attribute should be removed."""
        # Event handlers
        for pattern in FORBIDDEN_ATTR_PATTERNS:
            if pattern.match(stripped):
                return True

        # External href / xlink:href
        if stripped in ("href", "xlink:href") and value.strip():
            val_lower = value.strip().lower()
            if not val_lower.startswith("#"):
                return True  # external reference

        # style with url() — potential CSS injection
        if stripped == "style" and "url(" in value.lower():
            return True

        return False

    @staticmethod
    def _max_depth(el: Element) -> int:
        """Return maximum nesting depth of the tree."""
        if not list(el):
            return 1
        return 1 + max(SvgSanitizer._max_depth(child) for child in el)

    @staticmethod
    def _count_nodes(el: Element) -> int:
        """Return total number of XML nodes (including *el*)."""
        count = 1
        for child in el:
            count += SvgSanitizer._count_nodes(child)
        return count


def sanitize_svg(raw: bytes) -> bytes:
    """Convenience wrapper for one-shot sanitization."""
    return SvgSanitizer().sanitize(raw)


def content_hash(data: bytes) -> str:
    """SHA-256 content hash for deduplication and caching."""
    return "sha256:" + hashlib.sha256(data).hexdigest()

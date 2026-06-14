"""Candidate ranking service.

Combines multiple scoring dimensions into a single ``final_score``
for each ``AssetCandidate`` and sorts by descending score.

Scoring dimensions (configurable weights):

    final_score =
        0.40 × text_score
      + 0.20 × style_score
      + 0.15 × quality_score
      + 0.15 × editability_score
      + 0.10 × source_score
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from assets.domain.models import AssetCandidate

logger = logging.getLogger(__name__)

# Default scoring weights (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "text": 0.40,
    "style": 0.20,
    "quality": 0.15,
    "editability": 0.15,
    "source": 0.10,
}

# Negative keywords that cause text_score demotion
NEGATIVE_TERMS = {
    "sad", "angry", "crying", "evil", "scary", "broken", "ugly",
    "伤心", "生气", "哭", "可怕", "丑陋",
}

# Known high-trust providers
TRUSTED_SOURCES = {"iconify", "local"}

# Style keyword groups
STYLE_KEYWORDS = {
    "flat": {"flat", "扁平", "filled", "solid"},
    "outline": {"outline", "线条", "line", "stroke"},
    "cartoon": {"cartoon", "卡通", "可爱", "cute", "kawaii"},
    "minimal": {"minimal", "极简", "simple", "simplified"},
    "realistic": {"realistic", "写实", "真实", "photo", "photorealistic"},
    "sketch": {"sketch", "手绘", "handdrawn", "hand-drawn"},
}


class RankingService:
    """Ranks asset candidates by multi-dimensional relevance scoring."""

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    def rank(self, candidates: list[AssetCandidate], query: str,
             style: str | None = None) -> list[AssetCandidate]:
        """Score and sort candidates in descending order.

        Mutates each candidate's score fields and returns a new sorted list.
        """
        scored: list[AssetCandidate] = []
        for c in candidates:
            c.text_score = self._score_text(c, query)
            c.style_score = self._score_style(c, style or "")
            c.quality_score = self._score_quality(c)
            c.editability_score = self._score_editability(c)
            c.source_score = self._score_source(c)
            c.final_score = (
                self.weights["text"] * c.text_score
                + self.weights["style"] * c.style_score
                + self.weights["quality"] * c.quality_score
                + self.weights["editability"] * c.editability_score
                + self.weights["source"] * c.source_score
            )
            scored.append(c)

        scored.sort(key=lambda c: c.final_score, reverse=True)
        return scored

    # ── dimension scorers ──────────────────────────────────────────────

    @staticmethod
    def _score_text(candidate: AssetCandidate, query: str) -> float:
        """Text matching between the query and candidate metadata.

        Checks title, tags, description, and provider_asset_id.
        Penalises candidates whose metadata contains negative terms.
        """
        query_lower = query.lower()
        title_lower = (candidate.title or "").lower()
        tags_lower = " ".join(t.lower() for t in candidate.tags)
        desc_lower = (candidate.description or "").lower()
        id_lower = (candidate.provider_asset_id or "").lower()

        haystack = f"{title_lower} {tags_lower} {desc_lower} {id_lower}"

        # Boost for exact or near match in title
        if query_lower in title_lower or title_lower in query_lower:
            base = 0.9
        elif SequenceMatcher(None, query_lower, title_lower).ratio() > 0.6:
            base = 0.7
        elif query_lower in haystack:
            base = 0.5
        else:
            base = 0.1

        # Negative keyword penalty
        for term in NEGATIVE_TERMS:
            if term in haystack:
                base *= 0.3
                break

        return max(0.0, min(base, 1.0))

    @staticmethod
    def _score_style(candidate: AssetCandidate, style: str) -> float:
        """Match candidate tags/title against the requested style."""
        if not style:
            return 0.5  # neutral when no style requested

        style = style.lower()
        keywords = STYLE_KEYWORDS.get(style, {style})

        haystack = f"{(candidate.title or '').lower()} {' '.join(candidate.tags).lower()}"

        for kw in keywords:
            if kw in haystack:
                return 0.9

        return 0.3

    @staticmethod
    def _score_quality(candidate: AssetCandidate) -> float:
        """Estimate asset quality from available metadata.

        Quality heuristics:
        - Has reasonable dimensions (≥ 64px) → good
        - Small icons (< 32px) are penalised
        - Extreme aspect ratios are penalised
        - Iconify icons get a baseline trust bump
        - SVG format is preferred
        """
        score = 0.5  # neutral baseline

        if candidate.width and candidate.height:
            # Size penalty
            min_dim = min(candidate.width, candidate.height)
            if min_dim >= 64:
                score += 0.2
            elif min_dim < 32:
                score -= 0.1  # too small to be useful

            # Aspect ratio check
            ratio = candidate.width / max(candidate.height, 1)
            if ratio < 0.25 or ratio > 4.0:
                score -= 0.2  # extremely skewed

        if candidate.provider == "iconify":
            score += 0.1

        if candidate.format == "svg":
            score += 0.1

        return max(0.0, min(score, 1.0))

    @staticmethod
    def _score_editability(candidate: AssetCandidate) -> float:
        """Estimate how editable the asset is on the canvas.

        Structured SVG > single-path SVG > transparent PNG > JPEG.
        """
        fmt = (candidate.format or "").lower()
        if fmt == "svg":
            return 0.9
        if fmt in ("png", "webp"):
            return 0.6
        if fmt in ("jpg", "jpeg"):
            return 0.3
        return 0.5

    @staticmethod
    def _score_source(candidate: AssetCandidate) -> float:
        """Trust / reliability of the provider."""
        return 0.9 if candidate.provider in TRUSTED_SOURCES else 0.5

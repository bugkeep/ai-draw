"""DrawingModeRouter — lightweight intent classifier for user drawing requests.

The router sits between the user message and the agent loop.  It answers
*what kind of drawing* the user wants, so the appropriate tools and
prompts are selected.

Architecture (from the plan):

    User message
      ↓
    Drawing Intent Router
      ├── primitive         →  exact geometry tools
      ├── diagram           →  flowchart / architecture tools
      ├── vector_asset      →  search + import SVG
      ├── raster_asset      →  search + proxy real image
      ├── image_generation  →  text-to-image pipeline
      └── canvas_edit       →  modify existing objects

Phase 1 implementation uses deterministic rules only.  Future phases may
optionally use LLM-based classification for ambiguous cases.
"""

from __future__ import annotations

import logging
from typing import Any

from assets.domain.enums import DrawingMode
from assets.domain.models import DrawingRoute
from .route_rules import apply_rules

logger = logging.getLogger(__name__)

# Default fallback when nothing matches well enough.
_DEFAULT_MODE = DrawingMode.PRIMITIVE

# Thresholds for auto-selection vs. returning candidates.
AUTO_SELECT_THRESHOLD = 0.60
CANDIDATE_THRESHOLD = 0.30


class DrawingModeRouter:
    """Classifies user drawing requests into intent modes.

    Thread-safe (no mutable shared state beyond the rule list, which is
    immutable after construction).
    """

    def __init__(self, auto_select_threshold: float = AUTO_SELECT_THRESHOLD,
                 candidate_threshold: float = CANDIDATE_THRESHOLD):
        self.auto_select_threshold = auto_select_threshold
        self.candidate_threshold = candidate_threshold

    async def route(
        self,
        message: str,
        canvas_state: dict[str, Any] | None = None,
        history: list[dict] | None = None,
    ) -> DrawingRoute:
        """Classify a user message into a DrawingMode with confidence.

        Args:
            message: Raw user input (Chinese or English).
            canvas_state: Current Fabric.js canvas object list.
            history: Previous turns in the current session.

        Returns:
            A DrawingRoute with the best-matching mode.
        """
        scores = apply_rules(message, canvas_state, history)

        # Pick the mode with the highest score.
        best_mode: DrawingMode = _DEFAULT_MODE
        best_score: float = 0.0
        for mode, score in scores.items():
            if score > best_score:
                best_score = score
                best_mode = mode

        # Build the route description.
        subject = self._extract_subject(message)
        style = self._infer_style(message)
        requires_search = best_mode in (DrawingMode.VECTOR_ASSET, DrawingMode.RASTER_ASSET)
        requires_existing_obj = best_mode == DrawingMode.CANVAS_EDIT and bool(
            canvas_state and canvas_state.get("objects")
        )

        return DrawingRoute(
            mode=best_mode,
            confidence=min(best_score + 0.30, 1.0),
            subject=subject,
            style=style,
            reason=self._describe_route(best_mode, scores),
            requires_search=requires_search,
            requires_existing_object=requires_existing_obj,
        )

    def _extract_subject(self, message: str) -> str:
        """Crudely extract the core subject from the user message.

        Strips leading verbs like "画一个", "画", "找一张", etc.
        """
        cleaned = message.strip()
        for prefix in ["画一个", "画一只", "画一棵", "画一朵", "画一座", "画一条",
                        "找一张", "搜索", "帮我画", "给我画", "画"]:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                break
        # Strip trailing punctuation
        cleaned = cleaned.strip("，。, .！!？?")
        return cleaned or message

    def _infer_style(self, message: str) -> str | None:
        """Detect requested style keywords."""
        msg_lower = message.lower()
        if any(k in msg_lower for k in ("扁平", "flat", "极简", "minimal")):
            return "flat"
        if any(k in msg_lower for k in ("卡通", "cartoon", "可爱")):
            return "cartoon"
        if any(k in msg_lower for k in ("写实", "realistic", "真实")):
            return "realistic"
        if any(k in msg_lower for k in ("线条", "line", "outline", "轮廓")):
            return "outline"
        if any(k in msg_lower for k in ("手绘", "sketch", "手画")):
            return "sketch"
        return None

    @staticmethod
    def _describe_route(best: DrawingMode, scores: dict[DrawingMode, float]) -> str:
        """Build a short human-readable explanation of the routing decision."""
        top = sorted(scores.items(), key=lambda x: -x[1])[:3]
        desc = ", ".join(f"{m.value}={s:.2f}" for m, s in top if s > 0)
        return f"routed to {best.value} ({desc})" if desc else f"routed to {best.value} (no rules matched)"

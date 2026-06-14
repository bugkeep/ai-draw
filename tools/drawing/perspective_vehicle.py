"""Draw a detailed editable three-quarter-view vehicle."""

from __future__ import annotations

import colorsys
import re

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from .vector_composition import DrawVectorCompositionTool


HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _shade(color: str, lightness_delta: float) -> str:
    red, green, blue = (int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    lightness = max(0.0, min(1.0, lightness + lightness_delta))
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def build_perspective_vehicle_svg(body_color: str) -> str:
    """Return a layered low front three-quarter vehicle SVG."""
    dark = _shade(body_color, -0.22)
    darker = _shade(body_color, -0.34)
    light = _shade(body_color, 0.18)
    highlight = _shade(body_color, 0.32)
    return f"""<svg viewBox="0 0 900 560" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="bodySide" x1="0" y1="0" x2="0.95" y2="1">
    <stop offset="0" stop-color="{light}"/>
    <stop offset="0.42" stop-color="{body_color}"/>
    <stop offset="1" stop-color="{dark}"/>
  </linearGradient>
  <linearGradient id="bodyHood" x1="0" y1="0" x2="1" y2="0.6">
    <stop offset="0" stop-color="{highlight}"/>
    <stop offset="0.55" stop-color="{body_color}"/>
    <stop offset="1" stop-color="{dark}"/>
  </linearGradient>
  <linearGradient id="bodyFront" x1="0" y1="0.1" x2="1" y2="1">
    <stop offset="0" stop-color="{light}"/>
    <stop offset="0.46" stop-color="{body_color}"/>
    <stop offset="1" stop-color="{darker}"/>
  </linearGradient>
  <linearGradient id="glass" x1="0" y1="0" x2="1" y2="0.15">
    <stop offset="0" stop-color="#2d7298"/>
    <stop offset="0.45" stop-color="#1c3e4f"/>
    <stop offset="1" stop-color="#171d1e"/>
  </linearGradient>
  <linearGradient id="lampAmber" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#fff56d"/>
    <stop offset="0.72" stop-color="#756a26"/>
    <stop offset="1" stop-color="#202015"/>
  </linearGradient>
  <linearGradient id="bumperDark" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#747783"/>
    <stop offset="1" stop-color="#303342"/>
  </linearGradient>
  <radialGradient id="tire" cx="0.35" cy="0.3" r="0.75">
    <stop offset="0" stop-color="#9ca2aa"/>
    <stop offset="0.38" stop-color="#747b86"/>
    <stop offset="0.72" stop-color="#343843"/>
    <stop offset="1" stop-color="#111318"/>
  </radialGradient>
  <radialGradient id="rim" cx="0.35" cy="0.3" r="0.7">
    <stop offset="0" stop-color="#ffffff"/>
    <stop offset="0.38" stop-color="#d8d9d8"/>
    <stop offset="0.72" stop-color="#8e8e8b"/>
    <stop offset="1" stop-color="#4c4c4a"/>
  </radialGradient>
</defs>

<g id="perspective_guides" opacity="0.12">
  <line x1="92" y1="270" x2="850" y2="150" stroke="#1b3744" stroke-width="2"/>
  <line x1="83" y1="422" x2="856" y2="326" stroke="#1b3744" stroke-width="2"/>
  <line x1="322" y1="160" x2="856" y2="150" stroke="#1b3744" stroke-width="2"/>
</g>
<ellipse id="cast_shadow" cx="445" cy="458" rx="385" ry="49" fill="#071018" opacity="0.32"/>
<path id="underbody_black_lip" d="M57 425 C79 470 165 492 333 501 C447 507 579 493 633 455
  L805 391 C827 382 855 386 870 397 L860 418 C805 423 754 421 693 418
  C575 499 366 533 158 495 C75 480 40 452 43 427 Z"
  fill="#171b1e" opacity="0.9"/>

<ellipse id="rear_tire" cx="816" cy="365" rx="43" ry="67" fill="url(#tire)" stroke="#111318" stroke-width="8"/>
<ellipse id="front_tire" cx="579" cy="397" rx="78" ry="118" fill="url(#tire)" stroke="#111318" stroke-width="10"/>

<path id="body_side_plane" d="M88 337 C95 293 134 265 217 246 L369 215 L458 145
  C495 118 674 108 743 124 C810 140 852 192 881 281 C895 289 902 312 895 347
  L881 398 L832 409 C829 369 807 337 777 331 C741 324 714 350 705 403
  L657 421 C656 332 622 280 570 275 C515 270 471 315 454 426 L252 430
  C189 430 128 422 80 402 C55 391 48 370 60 355 Z"
  fill="url(#bodySide)" stroke="#101417" stroke-width="5"/>

<path id="front_plane" d="M63 355 C64 326 78 300 105 284 L189 258 L269 275
  L236 359 L251 431 L82 410 C54 399 44 380 55 361 Z"
  fill="url(#bodyFront)" stroke="#101417" stroke-width="5"/>
<path id="hood_top_plane" d="M178 251 C255 228 339 215 456 209 L650 228 L483 283
  C346 272 239 276 111 312 C123 285 143 264 178 251 Z"
  fill="url(#bodyHood)" stroke="#102025" stroke-width="3"/>
<path id="cabin_roof_plane" d="M458 145 C503 113 674 108 744 124 C690 113 536 122 489 151
  L366 216 L326 211 Z" fill="{highlight}" opacity="0.72" stroke="{dark}" stroke-width="3"/>

<path id="windshield_glass" d="M370 214 L493 151 C535 132 648 130 680 132
  C690 133 696 140 692 150 L650 229 L480 226 Z"
  fill="url(#glass)" stroke="#121719" stroke-width="6"/>
<path id="side_window_glass" d="M705 130 C742 137 781 177 807 240 L721 247 L663 229 L700 133 Z"
  fill="url(#glass)" stroke="#121719" stroke-width="5"/>
<path id="rear_side_window_glass" d="M810 244 C833 253 853 273 866 300 L801 309 L726 251 Z"
  fill="url(#glass)" stroke="#121719" stroke-width="5"/>
<path id="a_pillar" d="M684 132 L651 228" fill="none" stroke="#0c1012" stroke-width="11" stroke-linecap="round"/>
<path id="b_pillar" d="M720 139 L723 250" fill="none" stroke="#0c1012" stroke-width="5"/>
<path id="window_belt_line" d="M648 229 L721 247 L807 240" fill="none" stroke="#111416" stroke-width="5"/>

<path id="front_wheel_arch" d="M453 428 C468 313 513 268 570 274 C623 280 658 332 657 421"
  fill="#8e949d" stroke="#111418" stroke-width="5"/>
<path id="rear_wheel_arch" d="M704 405 C713 348 740 322 777 330 C810 336 830 368 832 409"
  fill="#8e949d" stroke="#111418" stroke-width="5"/>
<path id="front_fender_plane" d="M451 286 L652 229 L705 403 L657 421
  C656 333 622 281 570 275 C515 270 471 315 454 426 Z"
  fill="{body_color}" opacity="0.42" stroke="{dark}" stroke-width="2"/>
<path id="front_door_plane" d="M653 229 L720 248 L704 405 L656 421 Z"
  fill="{body_color}" opacity="0.2" stroke="{dark}" stroke-width="2"/>
<path id="rear_door_plane" d="M721 249 L810 244 C852 269 874 315 866 382 L832 409 L704 405 Z"
  fill="{body_color}" opacity="0.16" stroke="{dark}" stroke-width="2"/>

<path id="front_grille_left" d="M81 315 L125 328 L117 378 L69 363 Z"
  fill="#050608" stroke="#1f2528" stroke-width="4"/>
<path id="center_grille" d="M129 321 C153 307 187 310 205 324 L200 382
  C174 390 142 386 119 374 Z" fill="#050608" stroke="#1f2528" stroke-width="5"/>
<path id="right_grille" d="M212 328 L318 333 L299 383 L191 379 Z"
  fill="#050608" stroke="#1f2528" stroke-width="5"/>
<path id="left_marker_light" d="M54 313 C59 291 70 277 85 270 L105 285 L93 329 Z"
  fill="url(#lampAmber)" stroke="#3c3515" stroke-width="3"/>
<path id="right_headlight" d="M315 333 C364 326 417 326 456 338 C470 343 470 363 455 376
  L330 381 L298 383 Z" fill="url(#lampAmber)" stroke="#22251a" stroke-width="4" opacity="0.9"/>
<path id="front_bumper" d="M64 383 L255 405 L286 477 L92 441 C52 430 38 408 48 389 Z"
  fill="url(#bumperDark)" stroke="#111317" stroke-width="4"/>
<path id="bumper_center_intake" d="M146 405 L232 413 L240 427 L148 420 Z"
  fill="#050607" stroke="#1d2029" stroke-width="3"/>
<path id="lower_splitter" d="M47 431 C118 463 264 479 451 465 L626 444 L620 472
  C493 520 214 523 77 464 C45 450 35 436 47 431 Z" fill="{dark}" stroke="#0c1113" stroke-width="4"/>
<path id="front_fog_lamp" d="M403 424 C434 416 469 415 486 423 C472 436 427 443 397 437 Z"
  fill="#e66d24" stroke="#5a2811" stroke-width="3"/>

<path id="side_skirt" d="M630 422 L831 383 L850 398 L653 458 Z"
  fill="#14181d" stroke="#0d1014" stroke-width="4"/>
<path id="upper_side_seam" d="M472 283 C548 267 625 253 717 250" fill="none" stroke="#0d5958" stroke-width="3" opacity="0.8"/>
<path id="body_belt_line" d="M91 356 C245 387 409 383 548 364 L873 315"
  fill="none" stroke="#075957" stroke-width="3" opacity="0.85"/>
<path id="lower_body_seam" d="M83 408 C227 442 404 443 560 416 L852 361"
  fill="none" stroke="#073d3e" stroke-width="3" opacity="0.82"/>
<path id="front_door_seam" d="M650 230 C660 279 661 346 651 421" fill="none" stroke="#073d3e" stroke-width="3"/>
<path id="rear_door_seam" d="M722 249 C729 293 728 354 713 405" fill="none" stroke="#073d3e" stroke-width="3"/>
<path id="hood_center_line" d="M202 259 C319 251 427 249 601 228" fill="none" stroke="#08706e" stroke-width="3" opacity="0.65"/>
<path id="hood_outer_line" d="M128 312 C235 277 370 257 531 233" fill="none" stroke="#08706e" stroke-width="3" opacity="0.72"/>
<path id="body_highlight" d="M112 301 C217 255 348 231 493 226" fill="none"
  stroke="{highlight}" stroke-width="4" stroke-linecap="round" opacity="0.45"/>
<path id="glass_reflection_highlight" d="M396 204 C467 166 538 148 625 147" fill="none"
  stroke="#6db5d2" stroke-width="5" stroke-linecap="round" opacity="0.38"/>

<path id="side_mirror" d="M680 230 C705 214 745 216 752 233 L750 255 C721 261 694 259 679 249 Z"
  fill="{body_color}" stroke="#0d1517" stroke-width="4"/>
<path id="front_door_handle" d="M704 294 L742 289 L750 296 L708 302 Z"
  fill="{dark}" stroke="{highlight}" stroke-width="2"/>
<path id="rear_door_handle" d="M812 286 L844 283 L850 289 L817 294 Z"
  fill="{dark}" stroke="{highlight}" stroke-width="2"/>

<ellipse id="rear_rim" cx="816" cy="365" rx="27" ry="43" fill="url(#rim)" stroke="#4b4d52" stroke-width="4"/>
<ellipse id="rear_inner_rim" cx="816" cy="365" rx="18" ry="31" fill="#d9d9d8" stroke="#757775" stroke-width="3"/>
<ellipse id="rear_hub" cx="816" cy="365" rx="8" ry="17" fill="#979794" stroke="#555753" stroke-width="2"/>
<ellipse id="front_rim" cx="579" cy="397" rx="51" ry="78" fill="url(#rim)" stroke="#4b4d52" stroke-width="6"/>
<ellipse id="front_inner_rim" cx="579" cy="397" rx="35" ry="56" fill="#ececeb" stroke="#8e908d" stroke-width="4"/>
<ellipse id="front_hub" cx="579" cy="397" rx="17" ry="33" fill="#a2a19c" stroke="#555753" stroke-width="3"/>
<path id="front_wheel_spokes" d="M579 321 L579 473 M532 397 L626 397 M545 346 L613 448 M614 346 L544 448"
  fill="none" stroke="#777a7f" stroke-width="5" opacity="0.9"/>
<path id="rear_wheel_spokes" d="M816 323 L816 407 M793 365 L839 365 M801 336 L832 394 M832 336 L800 394"
  fill="none" stroke="#777a7f" stroke-width="3" opacity="0.85"/>
</svg>"""


class DrawPerspectiveVehicleTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_perspective_vehicle",
            description=(
                "Draw a detailed editable low front three-quarter-view vehicle with a coherent sedan body, "
                "long hood, cabin, windshield, side windows, front/side/top planes, perspective-scaled wheels, "
                "wheel arches, bumpers, grille, headlights, door seams, side skirt, cast shadow, and highlights. "
                "Use for car or vehicle requests that mention 3D, perspective, realistic structure, or a "
                "complex/detailed appearance."
            ),
            parameters=[
                ToolParameter(
                    name="body_color",
                    type="string",
                    description="Six-digit hex body color, for example #1677ff",
                    default="#1677ff",
                ),
                ToolParameter(name="object_id", type="string", description="Stable object ID", default="perspective_car"),
                ToolParameter(name="left", type="number", description="Canvas X position", default=35),
                ToolParameter(name="top", type="number", description="Canvas Y position", default=80),
                ToolParameter(name="width", type="number", description="Rendered width", default=720),
                ToolParameter(name="height", type="number", description="Rendered height", default=420),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        body_color = str(kwargs.get("body_color", "#1677ff"))
        if not HEX_COLOR_RE.fullmatch(body_color):
            return ToolResult(
                is_error=True,
                error="body_color must be a six-digit hex color such as #1677ff",
                error_type="invalid_args",
            )

        result = DrawVectorCompositionTool().execute(
            svg=build_perspective_vehicle_svg(body_color),
            object_id=kwargs.get("object_id") or "perspective_car",
            semantic_type="perspective_vehicle",
            left=kwargs.get("left", 35),
            top=kwargs.get("top", 80),
            width=kwargs.get("width", 720),
            height=kwargs.get("height", 420),
        )
        if not result.is_error:
            result.description = (
                f"Drew detailed three-quarter-view vehicle '{kwargs.get('object_id') or 'perspective_car'}'"
            )
            result.data["type"] = "perspective_vehicle"
            result.data["body_color"] = body_color
        return result

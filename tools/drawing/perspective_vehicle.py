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
    """Return a layered front-right three-quarter vehicle SVG."""
    dark = _shade(body_color, -0.22)
    darker = _shade(body_color, -0.34)
    light = _shade(body_color, 0.18)
    highlight = _shade(body_color, 0.32)
    return f"""<svg viewBox="0 0 760 440" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="bodySide" x1="0" y1="0" x2="0.8" y2="1">
    <stop offset="0" stop-color="{light}"/>
    <stop offset="0.5" stop-color="{body_color}"/>
    <stop offset="1" stop-color="{dark}"/>
  </linearGradient>
  <linearGradient id="bodyFront" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{body_color}"/>
    <stop offset="1" stop-color="{darker}"/>
  </linearGradient>
  <linearGradient id="glass" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#dff5ff" stop-opacity="0.92"/>
    <stop offset="0.45" stop-color="#6097ba" stop-opacity="0.9"/>
    <stop offset="1" stop-color="#18374d" stop-opacity="0.96"/>
  </linearGradient>
  <radialGradient id="tire" cx="0.35" cy="0.3" r="0.75">
    <stop offset="0" stop-color="#4b5260"/>
    <stop offset="0.55" stop-color="#20242b"/>
    <stop offset="1" stop-color="#090b0e"/>
  </radialGradient>
  <radialGradient id="rim" cx="0.35" cy="0.3" r="0.7">
    <stop offset="0" stop-color="#ffffff"/>
    <stop offset="0.42" stop-color="#aeb8c4"/>
    <stop offset="1" stop-color="#4b5664"/>
  </radialGradient>
</defs>

<ellipse cx="392" cy="361" rx="294" ry="37" fill="#08111c" opacity="0.28"/>
<ellipse cx="222" cy="323" rx="58" ry="64" fill="url(#tire)" stroke="#05070a" stroke-width="8"/>
<ellipse cx="568" cy="317" rx="75" ry="82" fill="url(#tire)" stroke="#05070a" stroke-width="9"/>

<path d="M91 278 C108 238 157 215 229 201 L319 132 C351 106 457 103 502 124
  L605 187 C642 199 677 229 692 268 L681 302 L643 328 L640 311
  C634 268 605 244 568 244 C524 244 493 274 488 329 L306 333
  C302 282 270 254 226 254 C184 254 153 282 149 327 L116 318 Z"
  fill="url(#bodySide)" stroke="{darker}" stroke-width="4"/>

<path d="M605 187 C648 201 680 230 692 268 L681 302 L643 328 L628 283
  L606 246 L565 218 Z" fill="url(#bodyFront)" stroke="{darker}" stroke-width="4"/>
<path d="M502 124 L605 187 L565 218 L432 194 L397 137 Z"
  fill="{light}" stroke="{dark}" stroke-width="3"/>
<path d="M319 132 C351 106 457 103 502 124 L397 137 L285 202 L229 201 Z"
  fill="{highlight}" opacity="0.55"/>

<path d="M332 136 L397 128 L425 190 L290 198 Z" fill="url(#glass)" stroke="#d8f4ff" stroke-width="3"/>
<path d="M406 128 L490 133 L560 207 L438 190 Z" fill="url(#glass)" stroke="#d8f4ff" stroke-width="3"/>
<path d="M438 190 L560 207 L537 222 L444 207 Z" fill="#102d43" opacity="0.72"/>
<path d="M401 132 L438 190" fill="none" stroke="#132f44" stroke-width="5"/>

<path d="M289 205 L437 202 L476 326 L307 331 Z" fill="{body_color}" opacity="0.36"
  stroke="{dark}" stroke-width="2"/>
<path d="M438 202 L564 221 L490 326 L476 326 Z" fill="{dark}" opacity="0.24"
  stroke="{darker}" stroke-width="2"/>
<path d="M314 217 L424 213" fill="none" stroke="{light}" stroke-width="3" opacity="0.9"/>
<path d="M442 219 L536 232" fill="none" stroke="{light}" stroke-width="3" opacity="0.75"/>
<path d="M310 272 C370 263 435 265 493 278" fill="none" stroke="{darker}" stroke-width="3" opacity="0.7"/>
<path d="M120 289 C210 271 271 268 305 280" fill="none" stroke="{highlight}" stroke-width="5" opacity="0.65"/>

<path d="M529 207 L603 196 L638 215 L574 235 Z" fill="#e9fbff" stroke="#ffffff" stroke-width="3"/>
<path d="M604 250 L676 266 L679 288 L630 294 Z" fill="#101a25" stroke="#73818f" stroke-width="3"/>
<path d="M623 262 L671 273" fill="none" stroke="#b7c7d6" stroke-width="3"/>
<path d="M650 300 L681 286 L681 302 L647 325 Z" fill="#111923"/>
<path d="M111 286 L151 278 L148 302 L116 307 Z" fill="#ff3d52" stroke="#ffd4d9" stroke-width="2"/>
<path d="M249 207 L273 197 L284 207 L260 216 Z" fill="{darker}" stroke="{light}" stroke-width="2"/>
<path d="M444 211 L466 211 L475 218 L451 220 Z" fill="{darker}" stroke="{light}" stroke-width="2"/>

<ellipse cx="222" cy="323" rx="39" ry="44" fill="url(#rim)" stroke="#252d37" stroke-width="5"/>
<ellipse cx="568" cy="317" rx="52" ry="58" fill="url(#rim)" stroke="#252d37" stroke-width="6"/>
<ellipse cx="222" cy="323" rx="13" ry="15" fill="#36404d"/>
<ellipse cx="568" cy="317" rx="17" ry="19" fill="#36404d"/>
<path d="M222 282 L222 364 M185 323 L259 323 M196 294 L248 352 M248 294 L196 352"
  fill="none" stroke="#677585" stroke-width="5" opacity="0.9"/>
<path d="M568 264 L568 370 M520 317 L616 317 M534 278 L602 356 M602 278 L534 356"
  fill="none" stroke="#677585" stroke-width="6" opacity="0.9"/>

<path d="M151 328 C157 278 185 252 226 252 C270 252 302 281 307 333"
  fill="none" stroke="{highlight}" stroke-width="5"/>
<path d="M488 329 C493 274 524 243 568 243 C610 243 638 269 643 328"
  fill="none" stroke="{highlight}" stroke-width="6"/>
<path d="M175 221 C268 180 382 159 503 176" fill="none" stroke="#ffffff"
  stroke-width="7" stroke-linecap="round" opacity="0.28"/>
<path d="M325 147 C354 127 372 122 398 120" fill="none" stroke="#ffffff"
  stroke-width="5" stroke-linecap="round" opacity="0.48"/>
</svg>"""


class DrawPerspectiveVehicleTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="draw_perspective_vehicle",
            description=(
                "Draw a detailed editable front-right three-quarter-view vehicle with a coherent body, "
                "hood, cabin, windshield, side windows, front and side planes, perspective-scaled wheels, "
                "rims, lights, grille, seams, cast shadow, and highlights. Use for car or vehicle requests "
                "that mention 3D, perspective, realistic structure, or a complex/detailed appearance."
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

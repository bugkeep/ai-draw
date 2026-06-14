from tools.drawing.vector_composition import DrawVectorCompositionTool
from tools.drawing.perspective_vehicle import (
    DrawPerspectiveVehicleTool,
    build_perspective_vehicle_svg,
)
from tools.policy import PolicyDecision, ToolPolicy


DETAILED_CAR_SVG = """
<svg viewBox="0 0 800 500" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="paint"><stop offset="0%" stop-color="#60a5fa"/><stop offset="100%" stop-color="#1d4ed8"/></linearGradient>
  </defs>
  <ellipse cx="410" cy="420" rx="310" ry="34" fill="#000" opacity=".18"/>
  <path d="M110 330 Q150 230 300 210 L500 220 Q620 240 700 320 L650 380 L160 380 Z" fill="url(#paint)"/>
  <path d="M285 215 Q340 130 460 145 L545 230 Z" fill="#2563eb"/>
  <polygon points="330,205 365,155 445,165 475,220" fill="#bfdbfe"/>
  <polygon points="485,222 455,165 500,180 540,230" fill="#93c5fd"/>
  <ellipse cx="235" cy="375" rx="68" ry="78" fill="#111827"/>
  <ellipse cx="235" cy="375" rx="34" ry="43" fill="#94a3b8"/>
  <ellipse cx="585" cy="365" rx="58" ry="69" fill="#111827"/>
  <ellipse cx="585" cy="365" rx="29" ry="37" fill="#94a3b8"/>
  <path d="M125 320 Q300 275 660 310" fill="none" stroke="#dbeafe" stroke-width="9"/>
  <polygon points="650,300 700,320 665,345" fill="#fef3c7"/>
  <polygon points="120,315 155,300 145,340" fill="#fee2e2"/>
  <line x1="470" y1="232" x2="455" y2="345" stroke="#1e3a8a" stroke-width="5"/>
  <path d="M180 280 Q360 225 600 275" fill="none" stroke="#fff" stroke-width="8" opacity=".45"/>
</svg>
"""


def test_draw_vector_composition_generates_sanitized_fabric_group():
    result = DrawVectorCompositionTool().execute(
        svg=DETAILED_CAR_SVG,
        object_id="perspective_car",
        semantic_type="vehicle",
        left=60,
        top=80,
        width=680,
        height=430,
    )

    assert not result.is_error
    assert result.data["visible_elements"] >= 10
    assert "fabric.loadSVGFromString" in result.code
    assert "fabric.util.groupSVGElements" in result.code
    assert "perspective_car" in result.code


def test_draw_vector_composition_removes_script_and_event_handlers():
    unsafe_svg = DETAILED_CAR_SVG.replace(
        "</svg>",
        '<script>alert(1)</script><circle cx="1" cy="1" r="1" onclick="alert(2)"/></svg>',
    )

    result = DrawVectorCompositionTool().execute(svg=unsafe_svg)

    assert not result.is_error
    assert "<script" not in result.code
    assert "onclick" not in result.code


def test_draw_vector_composition_rejects_placeholder_svg():
    result = DrawVectorCompositionTool().execute(
        svg='<svg viewBox="0 0 100 100"><rect width="100" height="100"/></svg>',
    )

    assert result.is_error
    assert "at least 3 visible" in result.error


def test_draw_vector_composition_is_auto_allowed():
    assert ToolPolicy().evaluate("draw_vector_composition") == PolicyDecision.ALLOW


def test_perspective_vehicle_has_detailed_three_quarter_structure():
    svg = build_perspective_vehicle_svg("#1677ff")
    result = DrawPerspectiveVehicleTool().execute(
        body_color="#1677ff",
        object_id="sports_car",
    )

    assert not result.is_error
    assert result.data["type"] == "perspective_vehicle"
    assert result.data["visible_elements"] >= 30
    assert "bodySide" in svg
    assert "bodyFront" in svg
    assert "glass" in svg
    assert "sports_car" in result.code


def test_perspective_vehicle_rejects_unsafe_color():
    result = DrawPerspectiveVehicleTool().execute(body_color="url(javascript:alert(1))")

    assert result.is_error
    assert "six-digit hex color" in result.error


def test_perspective_vehicle_is_auto_allowed():
    assert ToolPolicy().evaluate("draw_perspective_vehicle") == PolicyDecision.ALLOW

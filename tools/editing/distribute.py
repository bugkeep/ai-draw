from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class DistributeObjectsTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="distribute_objects",
            description="Evenly distribute all objects horizontally or vertically",
            parameters=[
                ToolParameter(name="axis", type="string", description="Distribution axis", required=True, enum=["horizontal", "vertical"]),
                ToolParameter(name="spacing", type="number", description="Optional exact spacing between object centers. If omitted, spread between first and last.", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        axis = kwargs.get("axis", "horizontal")
        spacing = kwargs.get("spacing")
        if axis not in ("horizontal", "vertical"):
            return ToolResult(is_error=True, error=f"Invalid distribution axis: {axis}")

        coord = "left" if axis == "horizontal" else "top"
        center = "centerX" if axis == "horizontal" else "centerY"
        spacing_js = "null" if spacing is None else str(float(spacing))
        code = f"""
const axis = {coord!r};
const centerKey = {center!r};
const requestedSpacing = {spacing_js};
const objects = canvas.getObjects().slice().sort((a, b) => {{
  const ar = a.getBoundingRect(true, true);
  const br = b.getBoundingRect(true, true);
  const ac = axis === 'left' ? ar.left + ar.width / 2 : ar.top + ar.height / 2;
  const bc = axis === 'left' ? br.left + br.width / 2 : br.top + br.height / 2;
  return ac - bc;
}});
if (objects.length >= 3) {{
  const centers = objects.map(obj => {{
    const rect = obj.getBoundingRect(true, true);
    return axis === 'left' ? rect.left + rect.width / 2 : rect.top + rect.height / 2;
  }});
  const start = centers[0];
  const gap = requestedSpacing === null ? (centers[centers.length - 1] - centers[0]) / (objects.length - 1) : requestedSpacing;
  objects.forEach((obj, index) => {{
    const rect = obj.getBoundingRect(true, true);
    const current = axis === 'left' ? rect.left + rect.width / 2 : rect.top + rect.height / 2;
    const target = start + gap * index;
    const delta = target - current;
    obj.set({{ [axis]: (obj[axis] || 0) + delta }});
    obj.setCoords();
  }});
  canvas.renderAll();
}}
""".strip()

        return ToolResult(
            code=code,
            description=f"Distributed objects on the {axis} axis",
            data={"axis": axis, "spacing": spacing},
        )

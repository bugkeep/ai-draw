from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


ALIGN_MODES = ["left", "center", "right", "top", "middle", "bottom"]


class AlignObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="align_object",
            description="Align an object or all objects to the canvas or to their collective bounds",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="mode", type="string", description="Alignment mode", required=True, enum=ALIGN_MODES),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        mode = kwargs.get("mode", "center")
        if mode not in ALIGN_MODES:
            return ToolResult(is_error=True, error=f"Invalid align mode: {mode}")

        if object_id:
            expr = f"[canvas.getObjects().find(o => o.objectId === '{object_id}')].filter(Boolean)"
            target = object_id
        elif selector == "all":
            expr = "canvas.getObjects()"
            target = "all objects"
        elif selector == "last":
            expr = "[canvas.getObjects().at(-1)].filter(Boolean)"
            target = "last object"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            expr = f"[canvas.getObjects()[{idx}]].filter(Boolean)"
            target = f"object at index {idx}"

        code = f"""
const objects = {expr};
if (objects.length) {{
  const canvasWidth = canvas.getWidth ? canvas.getWidth() : 800;
  const canvasHeight = canvas.getHeight ? canvas.getHeight() : 600;
  const bounds = objects.reduce((acc, obj) => {{
    const rect = obj.getBoundingRect(true, true);
    acc.left = Math.min(acc.left, rect.left);
    acc.top = Math.min(acc.top, rect.top);
    acc.right = Math.max(acc.right, rect.left + rect.width);
    acc.bottom = Math.max(acc.bottom, rect.top + rect.height);
    return acc;
  }}, {{ left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity }});
  const boxWidth = bounds.right - bounds.left;
  const boxHeight = bounds.bottom - bounds.top;
  const targetLeft = {self._target_left_js(mode)};
  const targetTop = {self._target_top_js(mode)};
  const dx = targetLeft - bounds.left;
  const dy = targetTop - bounds.top;
  objects.forEach(obj => {{
    obj.set({{ left: (obj.left || 0) + dx, top: (obj.top || 0) + dy }});
    obj.setCoords();
  }});
  canvas.renderAll();
}}
""".strip()

        return ToolResult(
            code=code,
            description=f"Aligned {target} to {mode}",
            data={"object_id": object_id, "selector": selector, "mode": mode},
        )

    @staticmethod
    def _target_left_js(mode: str) -> str:
        if mode == "left":
            return "0"
        if mode in ("center", "middle"):
            return "(canvasWidth - boxWidth) / 2"
        if mode == "right":
            return "canvasWidth - boxWidth"
        return "bounds.left"

    @staticmethod
    def _target_top_js(mode: str) -> str:
        if mode == "top":
            return "0"
        if mode in ("center", "middle"):
            return "(canvasHeight - boxHeight) / 2"
        if mode == "bottom":
            return "canvasHeight - boxHeight"
        return "bounds.top"

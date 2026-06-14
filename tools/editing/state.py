import json

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


def _filter_js(obj_type: str, color: str) -> str:
    conditions = []
    if obj_type:
        obj_type_js = json.dumps(obj_type, ensure_ascii=False)
        conditions.append(f"(obj.type === {obj_type_js} || obj.semanticType === {obj_type_js})")
    if color:
        color_js = json.dumps(color, ensure_ascii=False)
        conditions.append(f"(obj.fill === {color_js} || obj.stroke === {color_js})")
    return " && ".join(conditions) if conditions else "true"


def _coerce_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off"}
    return bool(value)


def _target_expr(object_id: str, selector: str, obj_type: str, color: str) -> tuple[str, str] | ToolResult:
    if object_id:
        object_id_js = json.dumps(object_id, ensure_ascii=False)
        return f"[canvas.getObjects().find(o => o.objectId === {object_id_js})].filter(Boolean)", object_id
    if obj_type or color:
        return f"canvas.getObjects().filter(obj => {_filter_js(obj_type, color)})", "matching objects"
    if selector == "all":
        return "canvas.getObjects()", "all objects"
    if selector == "last":
        return "[canvas.getObjects().at(-1)].filter(Boolean)", "last object"
    try:
        idx = int(selector)
    except ValueError:
        return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
    return f"[canvas.getObjects()[{idx}]].filter(Boolean)", f"object at index {idx}"


class SetObjectLockTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="set_object_lock",
            description="Lock or unlock object interaction so voice users can protect objects from accidental edits",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="type", type="string", description="Optional object type or semantic_type filter", required=False),
                ToolParameter(name="color", type="string", description="Optional fill or stroke color filter", required=False),
                ToolParameter(name="locked", type="boolean", description="True to lock, false to unlock", required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "") or ""
        selector = kwargs.get("selector", "last") or "last"
        obj_type = kwargs.get("type", "") or ""
        color = kwargs.get("color", "") or ""
        locked = _coerce_bool(kwargs.get("locked"), True)
        target = _target_expr(object_id, selector, obj_type, color)
        if isinstance(target, ToolResult):
            return target
        expr, target_name = target
        locked_js = "true" if locked else "false"
        code = f"""
const objects = {expr};
objects.forEach(obj => {{
  obj.set({{
    selectable: !{locked_js},
    evented: !{locked_js},
    lockMovementX: {locked_js},
    lockMovementY: {locked_js},
    lockScalingX: {locked_js},
    lockScalingY: {locked_js},
    lockRotation: {locked_js},
    hasControls: !{locked_js}
  }});
  obj.lockedByVoice = {locked_js};
  obj.setCoords();
}});
const active = canvas.getActiveObject();
if ({locked_js} && active) {{
  const activeObjects = active.type === "activeSelection" ? active.getObjects() : [active];
  if (activeObjects.some(obj => objects.includes(obj))) canvas.discardActiveObject();
}}
canvas.renderAll();
""".strip()
        action = "Locked" if locked else "Unlocked"
        return ToolResult(
            code=code,
            description=f"{action} {target_name}",
            data={"object_id": object_id, "selector": selector, "type": obj_type, "color": color, "locked": locked},
        )


class SetObjectVisibilityTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="set_object_visibility",
            description="Hide or show objects on the canvas without deleting them",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="type", type="string", description="Optional object type or semantic_type filter", required=False),
                ToolParameter(name="color", type="string", description="Optional fill or stroke color filter", required=False),
                ToolParameter(name="visible", type="boolean", description="True to show, false to hide", required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "") or ""
        selector = kwargs.get("selector", "last") or "last"
        obj_type = kwargs.get("type", "") or ""
        color = kwargs.get("color", "") or ""
        visible = _coerce_bool(kwargs.get("visible"), True)
        target = _target_expr(object_id, selector, obj_type, color)
        if isinstance(target, ToolResult):
            return target
        expr, target_name = target
        visible_js = "true" if visible else "false"
        code = f"""
const objects = {expr};
objects.forEach(obj => {{
  obj.set({{ visible: {visible_js} }});
  obj.hiddenByVoice = !{visible_js};
  obj.setCoords();
}});
const active = canvas.getActiveObject();
if (!{visible_js} && active) {{
  const activeObjects = active.type === "activeSelection" ? active.getObjects() : [active];
  if (activeObjects.some(obj => objects.includes(obj))) canvas.discardActiveObject();
}}
canvas.renderAll();
""".strip()
        action = "Showed" if visible else "Hid"
        return ToolResult(
            code=code,
            description=f"{action} {target_name}",
            data={"object_id": object_id, "selector": selector, "type": obj_type, "color": color, "visible": visible},
        )

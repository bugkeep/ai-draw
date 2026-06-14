from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


ARRANGE_ACTIONS = ["bring_front", "send_back", "bring_forward", "send_backward"]


class ArrangeObjectTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="arrange_object",
            description="Change object stacking order: bring front, send back, bring forward, or send backward",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last' or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="action", type="string", description="Stacking action", required=True, enum=ARRANGE_ACTIONS),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        action = kwargs.get("action", "bring_front")
        if action not in ARRANGE_ACTIONS:
            return ToolResult(is_error=True, error=f"Invalid arrange action: {action}")

        method = {
            "bring_front": "bringToFront",
            "send_back": "sendToBack",
            "bring_forward": "bringForward",
            "send_backward": "sendBackwards",
        }[action]

        if object_id:
            expr = f"canvas.getObjects().find(o => o.objectId === '{object_id}')"
            target = object_id
        elif selector == "last":
            expr = "canvas.getObjects().at(-1)"
            target = "last object"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            expr = f"canvas.getObjects()[{idx}]"
            target = f"object at index {idx}"

        code = (
            f"const obj = {expr};"
            f"if (obj) {{ canvas.{method}(obj); canvas.setActiveObject(obj); canvas.renderAll(); }}"
        )
        return ToolResult(
            code=code,
            description=f"Applied {action} to {target}",
            data={"object_id": object_id, "selector": selector, "action": action},
        )

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


BLEND_MODES = [
    "source-over",
    "multiply",
    "screen",
    "overlay",
    "darken",
    "lighten",
    "color-dodge",
    "color-burn",
    "hard-light",
    "soft-light",
    "difference",
    "exclusion",
]


class ChangeBlendModeTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="change_blend_mode",
            description="Change an object's canvas blend/composite mode",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last', 'all', or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="mode", type="string", description="Blend mode", required=True, enum=BLEND_MODES),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        mode = kwargs.get("mode", "source-over")
        if mode not in BLEND_MODES:
            return ToolResult(is_error=True, error=f"Invalid blend mode: {mode}")

        if object_id:
            code = (
                f"const obj = canvas.getObjects().find(o => o.objectId === '{object_id}');"
                f"if (obj) {{ obj.set({{ globalCompositeOperation: '{mode}' }}); canvas.renderAll(); }}"
            )
            return ToolResult(
                code=code,
                description=f"Changed object '{object_id}' blend mode to {mode}",
                data={"object_id": object_id, "mode": mode},
            )

        if selector == "all":
            code = (
                f"canvas.getObjects().forEach(obj => obj.set({{ globalCompositeOperation: '{mode}' }}));"
                f"canvas.renderAll();"
            )
            target = "all objects"
        elif selector == "last":
            code = (
                f"const obj = canvas.getObjects().at(-1);"
                f"if (obj) {{ obj.set({{ globalCompositeOperation: '{mode}' }}); canvas.renderAll(); }}"
            )
            target = "last object"
        else:
            try:
                idx = int(selector)
            except ValueError:
                return ToolResult(is_error=True, error=f"Invalid selector: {selector}")
            code = (
                f"const obj = canvas.getObjects()[{idx}];"
                f"if (obj) {{ obj.set({{ globalCompositeOperation: '{mode}' }}); canvas.renderAll(); }}"
            )
            target = f"object at index {idx}"

        return ToolResult(
            code=code,
            description=f"Changed {target} blend mode to {mode}",
            data={"selector": selector, "mode": mode},
        )

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class ApplyClipMaskTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_clip_mask",
            description="Use one object as the clip mask for another object",
            parameters=[
                ToolParameter(name="target_object_id", type="string", description="ObjectId of the object to be clipped", required=True),
                ToolParameter(name="mask_object_id", type="string", description="ObjectId of the mask shape", required=True),
                ToolParameter(name="remove_mask", type="boolean", description="Remove the original mask object after applying it (default: true)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        target_object_id = kwargs.get("target_object_id", "")
        mask_object_id = kwargs.get("mask_object_id", "")
        remove_mask = kwargs.get("remove_mask", True)
        if not target_object_id or not mask_object_id:
            return ToolResult(is_error=True, error="target_object_id and mask_object_id are required")
        if target_object_id == mask_object_id:
            return ToolResult(is_error=True, error="target and mask must be different objects")

        remove_js = "canvas.remove(mask);" if remove_mask else ""
        code = (
            f"const target = canvas.getObjects().find(o => o.objectId === '{target_object_id}');"
            f"const mask = canvas.getObjects().find(o => o.objectId === '{mask_object_id}');"
            "if (target && mask) {"
            "  const clip = fabric.util.object.clone(mask);"
            "  clip.set({ absolutePositioned: true, evented: false, selectable: false });"
            "  target.set({ clipPath: clip });"
            "  target.setCoords();"
            f"  {remove_js}"
            "  canvas.setActiveObject(target);"
            "  canvas.renderAll();"
            "}"
        )

        return ToolResult(
            code=code,
            description=f"Applied clip mask '{mask_object_id}' to '{target_object_id}'",
            data={
                "target_object_id": target_object_id,
                "mask_object_id": mask_object_id,
                "remove_mask": remove_mask,
            },
        )

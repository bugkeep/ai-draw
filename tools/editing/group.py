import json

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class GroupObjectsTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="group_objects",
            description="Group multiple canvas objects so they can be edited together",
            parameters=[
                ToolParameter(name="object_ids", type="array", description="Object IDs to group. If omitted and selector='all', group all objects.", required=False, items={"type": "string"}),
                ToolParameter(name="selector", type="string", description="Use 'all' to group all canvas objects when object_ids is omitted", required=False),
                ToolParameter(name="group_id", type="string", description="Stable objectId for the new group", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_ids = kwargs.get("object_ids") or []
        selector = kwargs.get("selector", "")
        group_id = kwargs.get("group_id") or "group_${Date.now()}"

        if object_ids and not isinstance(object_ids, list):
            return ToolResult(is_error=True, error="object_ids must be an array")
        if not object_ids and selector != "all":
            return ToolResult(is_error=True, error="Specify object_ids or selector='all' to group objects")

        ids_js = json.dumps(object_ids, ensure_ascii=False)
        group_id_js = json.dumps(group_id, ensure_ascii=False) if "${" not in group_id else "`group_${Date.now()}`"
        source_js = (
            "canvas.getObjects().filter(obj => ids.includes(obj.objectId))"
            if object_ids else
            "canvas.getObjects().slice()"
        )
        code = (
            "await (async () => {"
            f"const ids = {ids_js};"
            f"const objects = {source_js};"
            "if (objects.length >= 2) {"
            "  objects.forEach(obj => canvas.remove(obj));"
            "  const group = new fabric.Group(objects);"
            f"  group.set({{ objectId: {group_id_js}, semanticType: 'group' }});"
            "  canvas.add(group);"
            "  canvas.setActiveObject(group);"
            "  canvas.renderAll();"
            "}"
            "})();"
        )

        return ToolResult(
            code=code,
            description=f"Grouped {len(object_ids) if object_ids else 'all'} objects",
            data={"object_ids": object_ids, "selector": selector, "group_id": group_id},
        )


class UngroupObjectsTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="ungroup_objects",
            description="Ungroup a Fabric group back into individual objects",
            parameters=[
                ToolParameter(name="object_id", type="string", description="Group objectId to ungroup", required=False),
                ToolParameter(name="selector", type="string", description="Object selector: 'last' or index number (ignored when object_id is set)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")

        if object_id:
            expr = f"canvas.getObjects().find(o => o.objectId === {json.dumps(object_id)})"
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
            "await (async () => {"
            f"const group = {expr};"
            "if (group && group.type === 'group' && group.getObjects) {"
            "  const items = group.getObjects();"
            "  group._restoreObjectsState();"
            "  canvas.remove(group);"
            "  items.forEach(obj => { obj.setCoords(); canvas.add(obj); });"
            "  canvas.discardActiveObject();"
            "  canvas.renderAll();"
            "}"
            "})();"
        )

        return ToolResult(
            code=code,
            description=f"Ungrouped {target}",
            data={"object_id": object_id, "selector": selector},
        )

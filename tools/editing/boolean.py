import json

from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


BOOLEAN_OPERATIONS = ["union", "intersect", "subtract", "exclude"]


def _json(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


class BooleanShapeOperationTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="boolean_shape_operation",
            description=(
                "Create an editable compound object from two Fabric objects using "
                "object-level boolean operations: union, intersect, subtract, or exclude"
            ),
            parameters=[
                ToolParameter(name="target_object_id", type="string", description="Base objectId, e.g. the shape to subtract from", required=True),
                ToolParameter(name="source_object_id", type="string", description="Second objectId, e.g. the shape used as cutter/mask", required=True),
                ToolParameter(name="operation", type="string", description="Boolean operation", required=True, enum=BOOLEAN_OPERATIONS),
                ToolParameter(name="result_object_id", type="string", description="Stable objectId for the compound result", required=False),
                ToolParameter(name="remove_originals", type="boolean", description="Remove source and target after creating the result (default: true)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        target_object_id = kwargs.get("target_object_id", "") or ""
        source_object_id = kwargs.get("source_object_id", "") or ""
        operation = kwargs.get("operation", "") or ""
        result_object_id = kwargs.get("result_object_id") or f"boolean_{operation}_${{Date.now()}}"
        remove_originals = kwargs.get("remove_originals", True)
        if not target_object_id or not source_object_id:
            return ToolResult(is_error=True, error="target_object_id and source_object_id are required")
        if target_object_id == source_object_id:
            return ToolResult(is_error=True, error="target and source objects must be different")
        if operation not in BOOLEAN_OPERATIONS:
            return ToolResult(is_error=True, error=f"Invalid boolean operation: {operation}")

        result_id_js = _json(result_object_id) if "${" not in result_object_id else f"`{result_object_id}`"
        remove_js = "canvas.remove(target); canvas.remove(source);" if remove_originals else ""
        code = f"""
await (async () => {{
  const target = canvas.getObjects().find(o => o.objectId === {_json(target_object_id)});
  const source = canvas.getObjects().find(o => o.objectId === {_json(source_object_id)});
  if (!target || !source) return;

  const cloneObject = obj => new Promise(resolve => {{
    if (obj.clone) {{
      const maybeClone = obj.clone(clone => resolve(clone));
      if (maybeClone && typeof maybeClone.then === "function") {{
        maybeClone.then(clone => resolve(clone));
      }} else if (maybeClone) {{
        resolve(maybeClone);
      }}
    }} else {{
      resolve(fabric.util.object.clone(obj));
    }}
  }});
  const prepareClone = async obj => {{
    const clone = await cloneObject(obj);
    clone.set({{ evented: true, selectable: true, clipPath: null }});
    clone.setCoords();
    return clone;
  }};
  const prepareClip = async obj => {{
    const clip = await cloneObject(obj);
    clip.set({{ absolutePositioned: true, evented: false, selectable: false, clipPath: null }});
    clip.setCoords();
    return clip;
  }};

  let result = null;
  if ({_json(operation)} === "union") {{
    const targetClone = await prepareClone(target);
    const sourceClone = await prepareClone(source);
    result = new fabric.Group([targetClone, sourceClone], {{ objectCaching: false }});
  }} else if ({_json(operation)} === "intersect") {{
    result = await prepareClone(target);
    const clip = await prepareClip(source);
    result.set({{ clipPath: clip }});
  }} else if ({_json(operation)} === "subtract") {{
    result = await prepareClone(target);
    const clip = await prepareClip(source);
    clip.inverted = true;
    result.set({{ clipPath: clip }});
  }} else if ({_json(operation)} === "exclude") {{
    const targetPart = await prepareClone(target);
    const sourceClip = await prepareClip(source);
    sourceClip.inverted = true;
    targetPart.set({{ clipPath: sourceClip }});

    const sourcePart = await prepareClone(source);
    const targetClip = await prepareClip(target);
    targetClip.inverted = true;
    sourcePart.set({{ clipPath: targetClip }});
    result = new fabric.Group([targetPart, sourcePart], {{ objectCaching: false }});
  }}

  if (result) {{
    result.set({{
      objectId: {result_id_js},
      semanticType: "boolean_{operation}",
      objectCaching: false
    }});
    {remove_js}
    canvas.add(result);
    canvas.setActiveObject(result);
    canvas.renderAll();
  }}
}})();
""".strip()

        return ToolResult(
            code=code,
            description=f"Applied {operation} boolean operation to '{target_object_id}' and '{source_object_id}'",
            data={
                "target_object_id": target_object_id,
                "source_object_id": source_object_id,
                "operation": operation,
                "result_object_id": result_object_id,
                "remove_originals": remove_originals,
            },
        )

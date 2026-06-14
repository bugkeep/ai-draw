from ..base import BaseTool, ToolDefinition, ToolParameter, ToolResult


FILTER_TYPES = ["brightness", "contrast", "blur", "grayscale", "invert", "saturation"]


class ApplyImageFilterTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_image_filter",
            description="Apply a Fabric.js filter to an image object",
            parameters=[
                ToolParameter(name="selector", type="string", description="Object selector: 'last' or index number (ignored when object_id is set)", required=False),
                ToolParameter(name="object_id", type="string", description="Stable objectId assigned during creation", required=False),
                ToolParameter(name="filter_type", type="string", description="Filter type", required=True, enum=FILTER_TYPES),
                ToolParameter(name="value", type="number", description="Filter amount. Brightness/contrast/saturation usually -1..1; blur 0..1.", required=False),
                ToolParameter(name="replace_existing", type="boolean", description="Replace existing filter of the same type (default: true)", required=False),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        object_id = kwargs.get("object_id", "")
        selector = kwargs.get("selector", "last")
        filter_type = kwargs.get("filter_type", "")
        value = float(kwargs.get("value", 0.2))
        replace_existing = kwargs.get("replace_existing", True)
        if filter_type not in FILTER_TYPES:
            return ToolResult(is_error=True, error=f"Invalid filter_type: {filter_type}")
        if filter_type in ("brightness", "contrast", "saturation") and not -1 <= value <= 1:
            return ToolResult(is_error=True, error="value must be between -1 and 1 for this filter")
        if filter_type == "blur" and not 0 <= value <= 1:
            return ToolResult(is_error=True, error="blur value must be between 0 and 1")

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

        filter_js = self._filter_js(filter_type, value)
        replace_js = "true" if replace_existing else "false"
        code = f"""
const obj = {expr};
if (obj && obj.type === 'image' && obj.applyFilters) {{
  obj.filters = obj.filters || [];
  const filterType = {filter_type!r};
  if ({replace_js}) {{
    obj.filters = obj.filters.filter(filter => filter && filter.type !== filterType);
  }}
  const nextFilter = {filter_js};
  if (nextFilter) {{
    obj.filters.push(nextFilter);
    obj.applyFilters();
    obj.setCoords();
    canvas.setActiveObject(obj);
    canvas.renderAll();
  }}
}}
""".strip()

        return ToolResult(
            code=code,
            description=f"Applied {filter_type} filter to {target}",
            data={
                "object_id": object_id,
                "selector": selector,
                "filter_type": filter_type,
                "value": value,
                "replace_existing": replace_existing,
            },
        )

    @staticmethod
    def _filter_js(filter_type: str, value: float) -> str:
        if filter_type == "brightness":
            return f"new fabric.Image.filters.Brightness({{ brightness: {value} }})"
        if filter_type == "contrast":
            return f"new fabric.Image.filters.Contrast({{ contrast: {value} }})"
        if filter_type == "blur":
            return f"new fabric.Image.filters.Blur({{ blur: {value} }})"
        if filter_type == "grayscale":
            return "new fabric.Image.filters.Grayscale()"
        if filter_type == "invert":
            return "new fabric.Image.filters.Invert()"
        if filter_type == "saturation":
            return f"new fabric.Image.filters.Saturation({{ saturation: {value} }})"
        return "null"

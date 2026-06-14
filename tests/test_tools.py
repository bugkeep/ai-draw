import pytest
from tools.base import ToolResult
from tools.drawing.circle import DrawCircleTool
from tools.drawing.rect import DrawRectTool
from tools.drawing.line import DrawLineTool
from tools.drawing.text import DrawTextTool
from tools.drawing.ellipse import DrawEllipseTool
from tools.drawing.concentric_circles import DrawConcentricCirclesTool
from tools.editing.delete import DeleteObjectTool
from tools.editing.move import MoveObjectTool
from tools.editing.color import ChangeColorTool
from tools.editing.resize import ResizeObjectTool
from tools.editing.rotate import RotateObjectTool
from tools.editing.arrange import ArrangeObjectTool
from tools.editing.align import AlignObjectTool
from tools.editing.distribute import DistributeObjectsTool
from tools.editing.duplicate import DuplicateObjectTool
from tools.editing.group import GroupObjectsTool, UngroupObjectsTool
from tools.editing.opacity import ChangeOpacityTool
from tools.editing.stroke import ChangeStrokeTool
from tools.editing.select import SelectObjectTool
from tools.editing.crop import CropObjectTool
from tools.editing.mask import ApplyClipMaskTool
from tools.editing.blend import ChangeBlendModeTool
from tools.editing.filter import ApplyImageFilterTool
from tools.history.undo import UndoTool
from tools.history.redo import RedoTool
from tools.history.clear import ClearCanvasTool
from tools.registry import ToolRegistry


class TestDrawCircleTool:
    def test_definition(self):
        tool = DrawCircleTool()
        defn = tool.definition()
        assert defn.name == "draw_circle"
        assert any(p.name == "center_x" for p in defn.parameters)

    def test_execute_defaults(self):
        result = DrawCircleTool().execute()
        assert not result.is_error
        assert "fabric.Circle" in result.code
        assert result.data["type"] == "circle"

    def test_execute_custom(self):
        result = DrawCircleTool().execute(center_x=100, center_y=200, radius=30, color="red")
        assert not result.is_error
        assert "radius: 30" in result.code
        assert "'red'" in result.code
        assert result.data["center_x"] == 100


class TestDrawRectTool:
    def test_definition(self):
        defn = DrawRectTool().definition()
        assert defn.name == "draw_rect"

    def test_execute(self):
        result = DrawRectTool().execute(x=10, y=20, width=100, height=50, color="blue")
        assert not result.is_error
        assert "fabric.Rect" in result.code
        assert result.data["width"] == 100


class TestDrawLineTool:
    def test_definition(self):
        defn = DrawLineTool().definition()
        assert defn.name == "draw_line"

    def test_execute(self):
        result = DrawLineTool().execute(start_x=0, start_y=0, end_x=100, end_y=100, color="green", width=3)
        assert not result.is_error
        assert "fabric.Line" in result.code
        assert "strokeWidth: 3" in result.code


class TestDrawTextTool:
    def test_definition(self):
        defn = DrawTextTool().definition()
        assert defn.name == "draw_text"
        assert any(p.name == "text" for p in defn.parameters)

    def test_execute(self):
        result = DrawTextTool().execute(text="Hello", x=50, y=50, font_size=32)
        assert not result.is_error
        assert "fabric.Text" in result.code
        assert "Hello" in result.code

    def test_execute_empty_text(self):
        result = DrawTextTool().execute(text="")
        assert result.is_error
        assert "required" in result.error.lower()


class TestDrawEllipseTool:
    def test_definition(self):
        defn = DrawEllipseTool().definition()
        assert defn.name == "draw_ellipse"

    def test_execute(self):
        result = DrawEllipseTool().execute(center_x=200, center_y=200, rx=60, ry=40)
        assert not result.is_error
        assert "fabric.Ellipse" in result.code
        assert "rx: 60" in result.code


class TestDrawConcentricCirclesTool:
    def test_definition(self):
        defn = DrawConcentricCirclesTool().definition()
        assert defn.name == "draw_concentric_circles"
        assert "share exactly one center" in defn.description

    def test_execute_inner_green_outer_blue(self):
        result = DrawConcentricCirclesTool().execute(
            outer_color="blue",
            inner_color="green",
            outer_radius=120,
            inner_radius=55,
            center_x=400,
            center_y=300,
        )

        assert not result.is_error
        assert "fabric.Circle" in result.code
        assert '"left": 400.0' in result.code
        assert '"top": 300.0' in result.code
        assert '"fill": "blue"' in result.code
        assert '"fill": "green"' in result.code
        assert result.data["type"] == "concentric_circles"
        assert result.data["layers"][0]["color"] == "blue"
        assert result.data["layers"][1]["color"] == "green"
        assert result.data["layers"][0]["radius"] > result.data["layers"][1]["radius"]

    def test_execute_sorts_explicit_layers_outer_first(self):
        result = DrawConcentricCirclesTool().execute(
            layers=[
                {"name": "inner", "radius": 30, "color": "green"},
                {"name": "outer", "radius": 90, "color": "blue"},
            ]
        )

        assert not result.is_error
        assert result.data["layers"][0]["name"] == "outer"
        assert result.data["layers"][1]["name"] == "inner"


class TestDeleteObjectTool:
    def test_definition(self):
        defn = DeleteObjectTool().definition()
        assert defn.name == "delete_object"

    def test_execute_by_type(self):
        result = DeleteObjectTool().execute(type="circle")
        assert not result.is_error
        assert "fabric" in result.code or "canvas" in result.code

    def test_execute_no_params(self):
        result = DeleteObjectTool().execute()
        assert result.is_error


class TestDeleteObjectObjectId:
    def test_execute_by_object_id(self):
        result = DeleteObjectTool().execute(object_id="circle_1")
        assert not result.is_error
        assert "find" in result.code
        assert "objectId === 'circle_1'" in result.code
        assert result.data["object_id"] == "circle_1"

    def test_object_id_takes_priority(self):
        result = DeleteObjectTool().execute(object_id="rect_1", selector="last")
        assert not result.is_error
        assert "objectId === 'rect_1'" in result.code


class TestMoveObjectTool:
    def test_definition(self):
        defn = MoveObjectTool().definition()
        assert defn.name == "move_object"

    def test_execute_last(self):
        result = MoveObjectTool().execute(selector="last", x=10, y=20)
        assert not result.is_error
        assert "canvas" in result.code

    def test_execute_invalid_selector(self):
        result = MoveObjectTool().execute(selector="abc")
        assert result.is_error


class TestMoveObjectObjectId:
    def test_execute_by_object_id(self):
        result = MoveObjectTool().execute(object_id="circle_1", x=10, y=20)
        assert not result.is_error
        assert "find" in result.code
        assert "objectId === 'circle_1'" in result.code
        assert result.data["object_id"] == "circle_1"

    def test_object_id_takes_priority(self):
        result = MoveObjectTool().execute(object_id="rect_1", selector="last", x=5, y=5)
        assert not result.is_error
        assert "objectId === 'rect_1'" in result.code


class TestChangeColorTool:
    def test_definition(self):
        defn = ChangeColorTool().definition()
        assert defn.name == "change_color"

    def test_execute_last(self):
        result = ChangeColorTool().execute(selector="last", color="red")
        assert not result.is_error
        assert "'red'" in result.code

    def test_execute_all(self):
        result = ChangeColorTool().execute(selector="all", color="blue")
        assert not result.is_error
        assert "forEach" in result.code

    def test_execute_invalid_selector(self):
        result = ChangeColorTool().execute(selector="abc")
        assert result.is_error


class TestChangeColorObjectId:
    def test_execute_by_object_id(self):
        result = ChangeColorTool().execute(object_id="circle_1", color="red")
        assert not result.is_error
        assert "find" in result.code
        assert "objectId === 'circle_1'" in result.code
        assert "'red'" in result.code
        assert result.data["object_id"] == "circle_1"

    def test_object_id_takes_priority(self):
        result = ChangeColorTool().execute(object_id="rect_1", selector="last", color="blue")
        assert not result.is_error
        assert "objectId === 'rect_1'" in result.code


class TestResizeObjectTool:
    def test_definition(self):
        defn = ResizeObjectTool().definition()
        assert defn.name == "resize_object"

    def test_execute(self):
        result = ResizeObjectTool().execute(selector="last", scale_x=2.0, scale_y=1.5)
        assert not result.is_error
        assert "scaleX" in result.code


class TestResizeObjectObjectId:
    def test_execute_by_object_id(self):
        result = ResizeObjectTool().execute(object_id="circle_1", scale_x=2.0, scale_y=1.5)
        assert not result.is_error
        assert "find" in result.code
        assert "objectId === 'circle_1'" in result.code
        assert result.data["object_id"] == "circle_1"

    def test_object_id_takes_priority(self):
        result = ResizeObjectTool().execute(object_id="rect_1", selector="last", scale_x=0.5)
        assert not result.is_error
        assert "objectId === 'rect_1'" in result.code


class TestRotateObjectTool:
    def test_definition(self):
        defn = RotateObjectTool().definition()
        assert defn.name == "rotate_object"

    def test_execute_by_object_id(self):
        result = RotateObjectTool().execute(object_id="circle_1", degrees=45)
        assert not result.is_error
        assert "objectId === 'circle_1'" in result.code
        assert "+ 45.0" in result.code
        assert result.data["degrees"] == 45.0


class TestArrangeObjectTool:
    def test_definition(self):
        defn = ArrangeObjectTool().definition()
        assert defn.name == "arrange_object"

    def test_execute_bring_front(self):
        result = ArrangeObjectTool().execute(object_id="rect_1", action="bring_front")
        assert not result.is_error
        assert "bringToFront" in result.code
        assert result.data["action"] == "bring_front"


class TestAlignObjectTool:
    def test_definition(self):
        defn = AlignObjectTool().definition()
        assert defn.name == "align_object"

    def test_execute_center_all(self):
        result = AlignObjectTool().execute(selector="all", mode="center")
        assert not result.is_error
        assert "canvasWidth" in result.code
        assert "canvasHeight" in result.code
        assert "(canvasWidth - boxWidth) / 2" in result.code
        assert "(canvasHeight - boxHeight) / 2" in result.code
        assert result.data["mode"] == "center"


class TestDistributeObjectsTool:
    def test_definition(self):
        defn = DistributeObjectsTool().definition()
        assert defn.name == "distribute_objects"

    def test_execute_horizontal(self):
        result = DistributeObjectsTool().execute(axis="horizontal")
        assert not result.is_error
        assert "objects.length >= 3" in result.code
        assert "axis = 'left'" in result.code
        assert result.data["axis"] == "horizontal"


class TestDuplicateObjectTool:
    def test_definition(self):
        defn = DuplicateObjectTool().definition()
        assert defn.name == "duplicate_object"

    def test_execute_last(self):
        result = DuplicateObjectTool().execute(selector="last", offset_x=40, offset_y=10)
        assert not result.is_error
        assert "source.clone" in result.code
        assert "+ 40.0" in result.code
        assert "+ 10.0" in result.code


class TestGroupObjectsTool:
    def test_definition(self):
        defn = GroupObjectsTool().definition()
        assert defn.name == "group_objects"

    def test_execute_group_all(self):
        result = GroupObjectsTool().execute(selector="all", group_id="group_1")
        assert not result.is_error
        assert "new fabric.Group" in result.code
        assert "objectId: \"group_1\"" in result.code

    def test_execute_requires_source(self):
        result = GroupObjectsTool().execute()
        assert result.is_error


class TestUngroupObjectsTool:
    def test_definition(self):
        defn = UngroupObjectsTool().definition()
        assert defn.name == "ungroup_objects"

    def test_execute_last(self):
        result = UngroupObjectsTool().execute(selector="last")
        assert not result.is_error
        assert "_restoreObjectsState" in result.code


class TestChangeOpacityTool:
    def test_definition(self):
        defn = ChangeOpacityTool().definition()
        assert defn.name == "change_opacity"

    def test_execute_by_object_id(self):
        result = ChangeOpacityTool().execute(object_id="circle_1", opacity=0.5)
        assert not result.is_error
        assert "opacity: 0.5" in result.code
        assert result.data["opacity"] == 0.5

    def test_execute_rejects_invalid_opacity(self):
        result = ChangeOpacityTool().execute(opacity=1.5)
        assert result.is_error


class TestChangeStrokeTool:
    def test_definition(self):
        defn = ChangeStrokeTool().definition()
        assert defn.name == "change_stroke"

    def test_execute_all(self):
        result = ChangeStrokeTool().execute(selector="all", stroke="black", stroke_width=4)
        assert not result.is_error
        assert "stroke: 'black'" in result.code
        assert "strokeWidth: 4.0" in result.code


class TestSelectObjectTool:
    def test_definition(self):
        defn = SelectObjectTool().definition()
        assert defn.name == "select_object"

    def test_execute_by_type_and_color(self):
        result = SelectObjectTool().execute(type="circle", color="red")
        assert not result.is_error
        assert "semanticType === 'circle'" in result.code
        assert "obj.fill === 'red'" in result.code
        assert "ActiveSelection" in result.code


class TestCropObjectTool:
    def test_definition(self):
        defn = CropObjectTool().definition()
        assert defn.name == "crop_object"

    def test_execute_last_rect_crop(self):
        result = CropObjectTool().execute(selector="last", x=10, y=20, width=100, height=80)
        assert not result.is_error
        assert "clipPath" in result.code
        assert "new fabric.Rect" in result.code
        assert "width: 100.0" in result.code
        assert "height: 80.0" in result.code


class TestApplyClipMaskTool:
    def test_definition(self):
        defn = ApplyClipMaskTool().definition()
        assert defn.name == "apply_clip_mask"

    def test_execute_applies_mask(self):
        result = ApplyClipMaskTool().execute(target_object_id="image_1", mask_object_id="rect_1")
        assert not result.is_error
        assert "clipPath" in result.code
        assert "objectId === 'image_1'" in result.code
        assert "objectId === 'rect_1'" in result.code
        assert "canvas.remove(mask)" in result.code

    def test_rejects_same_target_and_mask(self):
        result = ApplyClipMaskTool().execute(target_object_id="same", mask_object_id="same")
        assert result.is_error


class TestChangeBlendModeTool:
    def test_definition(self):
        defn = ChangeBlendModeTool().definition()
        assert defn.name == "change_blend_mode"

    def test_execute_multiply_last(self):
        result = ChangeBlendModeTool().execute(selector="last", mode="multiply")
        assert not result.is_error
        assert "globalCompositeOperation: 'multiply'" in result.code
        assert result.data["mode"] == "multiply"

    def test_rejects_invalid_blend_mode(self):
        result = ChangeBlendModeTool().execute(mode="not-a-mode")
        assert result.is_error


class TestApplyImageFilterTool:
    def test_definition(self):
        defn = ApplyImageFilterTool().definition()
        assert defn.name == "apply_image_filter"

    def test_execute_brightness(self):
        result = ApplyImageFilterTool().execute(object_id="image_1", filter_type="brightness", value=0.25)
        assert not result.is_error
        assert "fabric.Image.filters.Brightness" in result.code
        assert "brightness: 0.25" in result.code
        assert "objectId === 'image_1'" in result.code

    def test_execute_grayscale(self):
        result = ApplyImageFilterTool().execute(selector="last", filter_type="grayscale")
        assert not result.is_error
        assert "fabric.Image.filters.Grayscale" in result.code

    def test_rejects_invalid_filter_value(self):
        result = ApplyImageFilterTool().execute(filter_type="blur", value=2)
        assert result.is_error


class TestUndoTool:
    def test_definition(self):
        defn = UndoTool().definition()
        assert defn.name == "undo"

    def test_execute(self):
        result = UndoTool().execute()
        assert not result.is_error


class TestRedoTool:
    def test_definition(self):
        defn = RedoTool().definition()
        assert defn.name == "redo"

    def test_execute(self):
        result = RedoTool().execute()
        assert not result.is_error


class TestClearCanvasTool:
    def test_definition(self):
        defn = ClearCanvasTool().definition()
        assert defn.name == "clear_canvas"

    def test_execute(self):
        result = ClearCanvasTool().execute()
        assert not result.is_error
        assert "canvas.clear()" in result.code


class TestToolRegistryIntegration:
    def test_register_all_drawing_tools(self):
        from tools import ALL_TOOLS
        reg = ToolRegistry()
        for tool_cls in ALL_TOOLS:
            reg.register(tool_cls())
        assert len(reg) == 42

    def test_get_definitions(self):
        from tools import ALL_TOOLS
        reg = ToolRegistry()
        for tool_cls in ALL_TOOLS:
            reg.register(tool_cls())
        defs = reg.get_tool_definitions()
        names = {d["function"]["name"] for d in defs}
        assert "draw_circle" in names
        assert "draw_concentric_circles" in names
        assert "draw_vector_composition" in names
        assert "draw_perspective_vehicle" in names
        assert "rotate_object" in names
        assert "arrange_object" in names
        assert "align_object" in names
        assert "distribute_objects" in names
        assert "duplicate_object" in names
        assert "group_objects" in names
        assert "ungroup_objects" in names
        assert "change_opacity" in names
        assert "change_stroke" in names
        assert "select_object" in names
        assert "crop_object" in names
        assert "apply_clip_mask" in names
        assert "change_blend_mode" in names
        assert "apply_image_filter" in names
        assert "undo" in names
        assert "clear_canvas" in names

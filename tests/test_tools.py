import pytest
from tools.base import ToolResult
from tools.drawing.circle import DrawCircleTool
from tools.drawing.rect import DrawRectTool
from tools.drawing.line import DrawLineTool
from tools.drawing.text import DrawTextTool
from tools.drawing.ellipse import DrawEllipseTool
from tools.editing.delete import DeleteObjectTool
from tools.editing.move import MoveObjectTool
from tools.editing.color import ChangeColorTool
from tools.editing.resize import ResizeObjectTool
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
        result = ChangeColorTool().execute(selector="abc", color="red")
        assert result.is_error


class TestResizeObjectTool:
    def test_definition(self):
        defn = ResizeObjectTool().definition()
        assert defn.name == "resize_object"

    def test_execute(self):
        result = ResizeObjectTool().execute(selector="last", scale_x=2.0, scale_y=1.5)
        assert not result.is_error
        assert "scaleX" in result.code


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
        assert len(reg) == 18

    def test_get_definitions(self):
        from tools import ALL_TOOLS
        reg = ToolRegistry()
        for tool_cls in ALL_TOOLS:
            reg.register(tool_cls())
        defs = reg.get_tool_definitions()
        names = {d["function"]["name"] for d in defs}
        assert "draw_circle" in names
        assert "undo" in names
        assert "clear_canvas" in names

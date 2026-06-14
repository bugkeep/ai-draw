import pytest
from providers.base import ToolCall, LLMResponse, LLMProvider
from tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from tools.registry import ToolRegistry


class TestToolCall:
    def test_to_dict(self):
        tc = ToolCall(id="call_1", name="draw_circle", arguments={"x": 10, "y": 20})
        d = tc.to_dict()
        assert d["id"] == "call_1"
        assert d["type"] == "function"
        assert d["function"]["name"] == "draw_circle"
        assert "10" in d["function"]["arguments"]

    def test_from_openai(self):
        data = {
            "id": "call_abc",
            "function": {
                "name": "draw_rect",
                "arguments": '{"x": 5, "y": 10, "width": 100}',
            },
        }
        tc = ToolCall.from_openai(data)
        assert tc.id == "call_abc"
        assert tc.name == "draw_rect"
        assert tc.arguments == {"x": 5, "y": 10, "width": 100}

    def test_from_openai_string_args(self):
        data = {
            "id": "call_1",
            "function": {"name": "tool", "arguments": '{"a": 1}'},
        }
        tc = ToolCall.from_openai(data)
        assert tc.arguments == {"a": 1}

    def test_from_openai_invalid_json(self):
        data = {
            "id": "call_1",
            "function": {"name": "tool", "arguments": "not-json"},
        }
        tc = ToolCall.from_openai(data)
        assert tc.arguments == {}

    def test_from_anthropic(self):
        data = {"id": "msg_1", "name": "draw_line", "input": {"x1": 0, "y1": 0}}
        tc = ToolCall.from_anthropic(data)
        assert tc.id == "msg_1"
        assert tc.name == "draw_line"
        assert tc.arguments == {"x1": 0, "y1": 0}


class TestLLMResponse:
    def test_to_dict(self):
        resp = LLMResponse(
            content="ok",
            model="gpt-4",
            tokens_used=100,
            tool_calls=[ToolCall(id="1", name="t", arguments={})],
        )
        d = resp.to_dict()
        assert d["content"] == "ok"
        assert d["model"] == "gpt-4"
        assert d["tokens_used"] == 100
        assert len(d["tool_calls"]) == 1


class TestToolDefinition:
    def test_to_openai(self):
        defn = ToolDefinition(
            name="draw_circle",
            description="Draw a circle",
            parameters=[
                ToolParameter(name="x", type="number", description="X coordinate", required=True),
                ToolParameter(name="color", type="string", description="Color", default="red"),
            ],
        )
        schema = defn.to_openai()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "draw_circle"
        params = schema["function"]["parameters"]
        assert "x" in params["properties"]
        assert "x" in params["required"]
        assert "color" not in params["required"]
        assert params["properties"]["color"]["default"] == "red"

    def test_to_anthropic(self):
        defn = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters=[ToolParameter(name="arg", type="string", description="An arg", required=True)],
        )
        schema = defn.to_anthropic()
        assert schema["name"] == "test_tool"
        assert schema["input_schema"]["required"] == ["arg"]


class TestToolResult:
    def test_success(self):
        r = ToolResult(code="c", description="d", data={"k": "v"})
        d = r.to_dict()
        assert d["is_error"] is False
        assert d["code"] == "c"
        assert d["description"] == "d"

    def test_failure(self):
        r = ToolResult(is_error=True, error="bad tool")
        d = r.to_dict()
        assert d["is_error"] is True
        assert d["error"] == "bad tool"


class DummyTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name="dummy", description="A dummy tool")

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(data=kwargs)


class FailTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name="fail_tool", description="Always fails")

    def execute(self, **kwargs) -> ToolResult:
        raise RuntimeError("boom")


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        assert reg.get("dummy") is not None
        assert reg.get("nonexistent") is None

    def test_register_all(self):
        reg = ToolRegistry()
        reg.register_all([DummyTool(), FailTool()])
        assert len(reg) == 2
        assert set(reg.list_tools()) == {"dummy", "fail_tool"}

    def test_get_tool_definitions(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        defs = reg.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "dummy"

    def test_execute_success(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        result = reg.execute("dummy", x=1, y=2)
        assert not result.is_error
        assert result.data == {"x": 1, "y": 2}

    def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = reg.execute("nope")
        assert result.is_error
        assert "Unknown tool" in result.error

    def test_execute_exception(self):
        reg = ToolRegistry()
        reg.register(FailTool())
        result = reg.execute("fail_tool")
        assert result.is_error
        assert "boom" in result.error

    def test_chained_register(self):
        reg = ToolRegistry()
        result = reg.register(DummyTool()).register(FailTool())
        assert result is reg
        assert len(reg) == 2

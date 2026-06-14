import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from agent.main import DrawingAgent, AgentResponse
from providers.base import LLMProvider, LLMResponse, ToolCall
from tools.base import ToolResult
from tools.registry import ToolRegistry
from tools import ALL_TOOLS
from events import EventBus, EventType


class MockProvider:
    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self._call_count = 0
        self.calls: list[dict] = []

    async def achat(self, messages, tools=None, model=None):
        self.calls.append({"messages": messages, "tools": tools})
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return LLMResponse(content="Done")

    def chat(self, messages, tools=None, model=None):
        raise NotImplementedError("Sync not used in tests")


class FailingProvider:
    async def achat(self, messages, tools=None, model=None):
        raise RuntimeError("LLM API error")

    def chat(self, messages, tools=None, model=None):
        raise RuntimeError("LLM API error")


def make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool_cls in ALL_TOOLS:
        reg.register(tool_cls())
    return reg


class TestDrawingAgentInit:
    def test_create_agent(self):
        provider = MockProvider([])
        registry = make_registry()
        agent = DrawingAgent(provider=provider, registry=registry)
        assert agent.max_tool_rounds == 5

    def test_custom_max_rounds(self):
        provider = MockProvider([])
        registry = make_registry()
        agent = DrawingAgent(provider=provider, registry=registry, max_tool_rounds=3)
        assert agent.max_tool_rounds == 3


class TestDrawingAgentNoTools:
    @pytest.mark.asyncio
    async def test_no_tool_calls(self):
        provider = MockProvider([LLMResponse(content="I drew a circle")])
        agent = DrawingAgent(provider=provider, registry=make_registry())
        result = await agent.chat("draw a circle")
        assert result.success
        assert result.content == "I drew a circle"
        assert result.code == ""
        assert result.tool_calls == []

    @pytest.mark.asyncio
    async def test_empty_response(self):
        provider = MockProvider([LLMResponse(content="")])
        agent = DrawingAgent(provider=provider, registry=make_registry())
        result = await agent.chat("hello")
        assert result.success
        assert result.content == ""


class TestDrawingAgentWithTools:
    @pytest.mark.asyncio
    async def test_single_tool_call(self):
        provider = MockProvider([
            LLMResponse(
                content="Drew a red circle",
                tool_calls=[ToolCall(id="1", name="draw_circle", arguments={"center_x": 100, "center_y": 100, "radius": 50, "color": "red"})],
            ),
            LLMResponse(content="Done"),
        ])
        agent = DrawingAgent(provider=provider, registry=make_registry())
        result = await agent.chat("draw a red circle")
        assert result.success
        assert "fabric.Circle" in result.code
        assert "Drew" in result.description
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "draw_circle"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self):
        provider = MockProvider([
            LLMResponse(
                content="Drawing two shapes",
                tool_calls=[
                    ToolCall(id="1", name="draw_circle", arguments={"center_x": 100, "center_y": 100, "color": "red"}),
                    ToolCall(id="2", name="draw_rect", arguments={"x": 200, "y": 200, "width": 80, "height": 60, "color": "blue"}),
                ],
            ),
            LLMResponse(content="Done"),
        ])
        agent = DrawingAgent(provider=provider, registry=make_registry())
        result = await agent.chat("draw a red circle and a blue rectangle")
        assert result.success
        assert "fabric.Circle" in result.code
        assert "fabric.Rect" in result.code
        assert len(result.tool_calls) == 2

    @pytest.mark.asyncio
    async def test_tool_rounds_loop(self):
        provider = MockProvider([
            LLMResponse(
                content="Step 1",
                tool_calls=[ToolCall(id="1", name="draw_circle", arguments={"color": "red"})],
            ),
            LLMResponse(
                content="Step 2",
                tool_calls=[ToolCall(id="2", name="draw_rect", arguments={"color": "blue"})],
            ),
            LLMResponse(content="All done"),
        ])
        agent = DrawingAgent(provider=provider, registry=make_registry())
        result = await agent.chat("draw something")
        assert result.success
        assert len(result.tool_calls) == 2
        assert result.content == "All done"


class TestDrawingAgentErrors:
    @pytest.mark.asyncio
    async def test_llm_error(self):
        provider = FailingProvider()
        agent = DrawingAgent(provider=provider, registry=make_registry())
        result = await agent.chat("draw something")
        assert not result.success
        assert "LLM call failed" in result.error

    @pytest.mark.asyncio
    async def test_tool_failure_continues(self):
        provider = MockProvider([
            LLMResponse(
                content="Trying bad tool",
                tool_calls=[ToolCall(id="1", name="nonexistent_tool", arguments={})],
            ),
            LLMResponse(content="Recovered"),
        ])
        agent = DrawingAgent(provider=provider, registry=make_registry())
        result = await agent.chat("do something")
        assert result.success
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["is_error"]


class TestDrawingAgentEventBus:
    @pytest.mark.asyncio
    async def test_dispatches_tool_events(self):
        bus = EventBus()
        events_received = []

        async def handler(event):
            events_received.append(event.event_type)

        bus.register(EventType.TOOL_CALL, handler)
        bus.register(EventType.TOOL_RESULT, handler)

        provider = MockProvider([
            LLMResponse(
                content="Drawing",
                tool_calls=[ToolCall(id="1", name="draw_circle", arguments={"color": "red"})],
            ),
            LLMResponse(content="Done"),
        ])
        agent = DrawingAgent(provider=provider, registry=make_registry(), event_bus=bus)
        await agent.chat("draw a circle")

        assert EventType.TOOL_CALL in events_received
        assert EventType.TOOL_RESULT in events_received

    @pytest.mark.asyncio
    async def test_dispatches_error_event(self):
        bus = EventBus()
        events_received = []

        async def handler(event):
            events_received.append(event.event_type)

        bus.register(EventType.ERROR, handler)

        agent = DrawingAgent(provider=FailingProvider(), registry=make_registry(), event_bus=bus)
        await agent.chat("draw")

        assert EventType.ERROR in events_received


class TestDrawingAgentCanvasState:
    @pytest.mark.asyncio
    async def test_canvas_state_in_prompt(self):
        provider = MockProvider([LLMResponse(content="ok")])
        agent = DrawingAgent(provider=provider, registry=make_registry())
        canvas = {
            "objects": [
                {"type": "circle", "left": 100, "top": 100, "fill": "red"},
                {"type": "rect", "left": 200, "top": 200, "fill": "blue"},
            ]
        }
        await agent.chat("add another", canvas_state=canvas)

        prompt_content = provider.calls[0]["messages"][0]["content"]
        assert "2 objects" in prompt_content
        assert "circle" in prompt_content
        assert "rect" in prompt_content

    @pytest.mark.asyncio
    async def test_empty_canvas(self):
        provider = MockProvider([LLMResponse(content="ok")])
        agent = DrawingAgent(provider=provider, registry=make_registry())
        await agent.chat("draw", canvas_state={})

        prompt_content = provider.calls[0]["messages"][0]["content"]
        assert "Empty canvas" in prompt_content


class TestDrawingAgentToDict:
    @pytest.mark.asyncio
    async def test_response_to_dict(self):
        provider = MockProvider([LLMResponse(content="hi")])
        agent = DrawingAgent(provider=provider, registry=make_registry())
        result = await agent.chat("hello")
        d = result.to_dict()
        assert "content" in d
        assert "code" in d
        assert "description" in d
        assert "tool_calls" in d

    @pytest.mark.asyncio
    async def test_error_response_to_dict(self):
        agent = DrawingAgent(provider=FailingProvider(), registry=make_registry())
        result = await agent.chat("fail")
        d = result.to_dict()
        assert "error" in d

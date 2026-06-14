import pytest
import re
import json
from unittest.mock import AsyncMock, MagicMock
from agent.runner import (
    AgentRunner, AgentConfig, AgentResponse,
    ToolResultStatus, new_run_id, classify_tool_error,
)
from providers.base import LLMProvider, LLMResponse, ToolCall
from tools.base import ToolResult
from tools.registry import ToolRegistry
from tools import ALL_TOOLS
from events import EventBus, EventType


class TestNewRunId:
    def test_format(self):
        rid = new_run_id()
        assert re.match(r"\d{6}-\d{6}-\d{6}", rid)

    def test_uniqueness(self):
        ids = {new_run_id() for _ in range(100)}
        assert len(ids) == 100

    def test_timestamp_component(self):
        import time
        rid = new_run_id()
        ts_part = rid[:13]
        assert len(ts_part) == 13
        assert ts_part[6] == "-"


class TestClassifyToolError:
    def test_success(self):
        result = ToolResult()
        assert classify_tool_error(result, "draw_circle", ToolRegistry()) == ToolResultStatus.SUCCESS

    def test_not_found(self):
        result = ToolResult(is_error=True, error="Unknown tool: nope")
        assert classify_tool_error(result, "nope", ToolRegistry()) == ToolResultStatus.FAILED_NOT_FOUND

    def test_invalid_args(self):
        result = ToolResult(is_error=True, error="Text content is required")
        assert classify_tool_error(result, "draw_text", ToolRegistry()) == ToolResultStatus.FAILED_INVALID_ARGS

    def test_execution_error(self):
        result = ToolResult(is_error=True, error="Tool execution failed: boom")
        assert classify_tool_error(result, "draw_circle", ToolRegistry()) == ToolResultStatus.FAILED_EXECUTION

    def test_unknown_error(self):
        result = ToolResult(is_error=True, error="something weird")
        assert classify_tool_error(result, "x", ToolRegistry()) == ToolResultStatus.FAILED_UNKNOWN


class TestAgentConfig:
    def test_requires_provider(self):
        with pytest.raises(ValueError, match="required"):
            AgentRunner(AgentConfig(provider=None))


def make_provider(responses):
    responses = list(responses)
    call_count = 0

    async def achat(messages, tools=None, model=None, **kwargs):
        nonlocal call_count
        if call_count < len(responses):
            resp = responses[call_count]
            call_count += 1
            return resp
        return LLMResponse(content="Done")

    provider = MagicMock()
    provider.achat = achat
    return provider


def make_registry():
    reg = ToolRegistry()
    for tool_cls in ALL_TOOLS:
        reg.register(tool_cls())
    return reg


class TestAgentRunnerPlan:
    @pytest.mark.asyncio
    async def test_plan_calls_provider(self):
        provider = make_provider([LLMResponse(content="hello")])
        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry()))
        result = await runner.run("hi")
        assert result.success
        assert result.content == "hello"

    @pytest.mark.asyncio
    async def test_plan_llm_error(self):
        async def fail_achat(**kwargs):
            raise RuntimeError("API down")
        provider = MagicMock()
        provider.achat = fail_achat

        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry()))
        result = await runner.run("test")
        assert not result.success
        assert "API down" in result.error


class TestAgentRunnerObserve:
    @pytest.mark.asyncio
    async def test_observe_merges_response(self):
        tc = ToolCall(id="c1", name="draw_circle", arguments={"color": "red"})
        provider = make_provider([
            LLMResponse(content="Drawing", tool_calls=[tc]),
            LLMResponse(content="Done"),
        ])
        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry()))
        result = await runner.run("draw")
        assert result.success
        assert len(result.tool_calls) == 1


class TestAgentRunnerAct:
    @pytest.mark.asyncio
    async def test_act_executes_tool(self):
        tc = ToolCall(id="c1", name="draw_circle", arguments={"center_x": 100, "center_y": 100, "radius": 50, "color": "red"})
        provider = make_provider([
            LLMResponse(content="Drawing", tool_calls=[tc]),
            LLMResponse(content="Done"),
        ])
        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry()))
        result = await runner.run("draw circle")
        assert "fabric.Circle" in result.code
        assert result.tool_calls[0]["is_error"] is False

    @pytest.mark.asyncio
    async def test_act_tool_failure(self):
        tc = ToolCall(id="c1", name="nonexistent_tool", arguments={})
        provider = make_provider([
            LLMResponse(content="Trying", tool_calls=[tc]),
            LLMResponse(content="Done"),
        ])
        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry()))
        result = await runner.run("do something")
        assert result.tool_calls[0]["is_error"] is True
        assert result.tool_calls[0]["status"] == "failed_not_found"


class TestAgentRunnerLoop:
    @pytest.mark.asyncio
    async def test_concentric_circle_request_uses_direct_shared_center_tool(self):
        async def should_not_call_llm(messages, tools=None, model=None, **kwargs):
            raise AssertionError("concentric circle requests should not need an LLM round")

        provider = MagicMock()
        provider.achat = should_not_call_llm
        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry()))

        result = await runner.run("生成一个同星圆，最里面是绿色，外层是蓝色")

        assert result.success
        assert result.rounds == 1
        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert call["name"] == "draw_concentric_circles"
        assert call["arguments"]["inner_color"] == "green"
        assert call["arguments"]["outer_color"] == "blue"
        assert "concentric_circles_outer" in result.code
        assert "concentric_circles_inner" in result.code

    @pytest.mark.asyncio
    async def test_multi_round(self):
        tc1 = ToolCall(id="c1", name="draw_circle", arguments={"color": "red"})
        tc2 = ToolCall(id="c2", name="draw_rect", arguments={"color": "blue"})

        call_count = 0
        async def achat(messages, tools=None, model=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(content="Step 1", tool_calls=[tc1])
            elif call_count == 2:
                return LLMResponse(content="Step 2", tool_calls=[tc2])
            return LLMResponse(content="Done")

        provider = MagicMock()
        provider.achat = achat

        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry(), max_rounds=5))
        result = await runner.run("draw two things")
        assert result.tool_calls[0]["name"] == "draw_circle"
        assert result.tool_calls[1]["name"] == "draw_rect"
        assert result.rounds == 3

    @pytest.mark.asyncio
    async def test_max_rounds_limit(self):
        call_count = 0
        async def achat(messages, tools=None, model=None, **kwargs):
            nonlocal call_count
            call_count += 1
            tc = ToolCall(id=f"c{call_count}", name="draw_circle", arguments={})
            return LLMResponse(content=f"Round {call_count}", tool_calls=[tc])

        provider = MagicMock()
        provider.achat = achat

        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry(), max_rounds=3))
        result = await runner.run("keep drawing")
        assert result.rounds == 3
        assert len(result.tool_calls) == 3

    @pytest.mark.asyncio
    async def test_stops_on_no_tool_calls(self):
        provider = make_provider([LLMResponse(content="All done")])
        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry()))
        result = await runner.run("hello")
        assert result.rounds == 1

    @pytest.mark.asyncio
    async def test_complex_scene_continues_past_early_finish_and_default_limit(self):
        call_count = 0

        async def achat(messages, tools=None, model=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count in (2, 8):
                return LLMResponse(content="Done")
            tc = ToolCall(
                id=f"c{call_count}",
                name="draw_circle",
                arguments={
                    "center_x": 100 + call_count * 20,
                    "center_y": 100,
                    "radius": 10,
                    "color": "green",
                },
            )
            return LLMResponse(content="Adding detail", tool_calls=[tc])

        provider = MagicMock()
        provider.achat = achat
        runner = AgentRunner(AgentConfig(
            provider=provider,
            registry=make_registry(),
            max_rounds=3,
        ))

        result = await runner.run("画一幅细节丰富的森林插画")

        assert len(result.tool_calls) == 6
        assert result.rounds == 8

    @pytest.mark.asyncio
    async def test_detailed_vector_composition_can_complete_complex_subject(self):
        detailed_svg = "<svg>" + "".join(
            f'<path d="M {i} 0 L {i + 1} 1"/>' for i in range(10)
        ) + "</svg>"
        provider = make_provider([
            LLMResponse(content="Drawing", tool_calls=[
                ToolCall(
                    id="car",
                    name="draw_vector_composition",
                    arguments={"svg": detailed_svg},
                ),
            ]),
            LLMResponse(content="Done"),
        ])
        registry = make_registry()
        from tools.drawing.vector_composition import DrawVectorCompositionTool
        registry.register(DrawVectorCompositionTool())
        runner = AgentRunner(AgentConfig(provider=provider, registry=registry))

        result = await runner.run("draw a detailed car in three-quarter perspective")

        assert result.rounds == 2
        assert result.tool_calls[0]["name"] == "draw_vector_composition"

    def test_complex_car_requires_structural_parts_when_using_primitives(self):
        calls = [
            {"name": "draw_rect", "arguments": {"object_id": "car_body"}, "is_error": False},
            {"name": "draw_circle", "arguments": {"object_id": "wheel_front"}, "is_error": False},
            {"name": "draw_circle", "arguments": {"object_id": "wheel_rear"}, "is_error": False},
            {"name": "draw_polygon", "arguments": {"object_id": "windshield"}, "is_error": False},
            {"name": "draw_ellipse", "arguments": {"object_id": "ground_shadow"}, "is_error": False},
            {"name": "draw_path", "arguments": {"object_id": "body_highlight"}, "is_error": False},
        ]

        assert not AgentRunner._complex_scene_incomplete(
            "image_generation",
            calls,
            "draw a 3D perspective car",
        )

        calls = [call for call in calls if "wheel" not in call["arguments"]["object_id"]]
        assert AgentRunner._complex_scene_incomplete(
            "image_generation",
            calls,
            "draw a 3D perspective car",
        )

    def test_perspective_vehicle_tool_completes_complex_car(self):
        calls = [
            {
                "name": "draw_perspective_vehicle",
                "arguments": {"body_color": "#1677ff"},
                "is_error": False,
            },
        ]

        assert not AgentRunner._complex_scene_incomplete(
            "image_generation",
            calls,
            "draw a 3D perspective car",
        )

    @pytest.mark.asyncio
    async def test_complex_continuation_repeats_original_request(self):
        captured_messages = []

        async def achat(messages, tools=None, model=None, **kwargs):
            captured_messages.append(messages)
            return LLMResponse(content="Done")

        provider = MagicMock()
        provider.achat = achat
        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry(), max_rounds=2))

        await runner.run("draw a 3D perspective car")

        assert any(
            "Original user request: draw a 3D perspective car" in message.get("content", "")
            for message in captured_messages[-1]
        )


class TestAgentRunnerEvents:
    @pytest.mark.asyncio
    async def test_dispatches_events(self):
        bus = EventBus()
        events = []

        async def handler(event):
            events.append(event)

        bus.register(EventType.AGENT_START, handler)
        bus.register(EventType.AGENT_STOP, handler)
        bus.register(EventType.LLM_REQUEST, handler)
        bus.register(EventType.LLM_RESPONSE, handler)
        bus.register(EventType.TOOL_CALL, handler)
        bus.register(EventType.TOOL_RESULT, handler)

        provider = make_provider([
            LLMResponse(content="Drawing", tool_calls=[
                ToolCall(id="c1", name="draw_circle", arguments={"color": "red"})
            ]),
            LLMResponse(content="Done"),
        ])
        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry(), event_bus=bus))
        await runner.run("draw")

        types = [e.event_type for e in events]

        # Full timeline: start → llm_req → llm_resp → tool_call → tool_result → llm_req → llm_resp → stop
        assert EventType.AGENT_START in types
        assert EventType.AGENT_STOP in types
        assert EventType.LLM_REQUEST in types
        assert EventType.LLM_RESPONSE in types
        assert EventType.TOOL_CALL in types
        assert EventType.TOOL_RESULT in types

        # Check ordering
        start_idx = types.index(EventType.AGENT_START)
        stop_idx = types.index(EventType.AGENT_STOP)
        assert start_idx < stop_idx

        # Check events carry run_id
        for e in events:
            assert e.run_id, f"event {e.event_type} missing run_id"

        # Check round_num in step-level events
        for e in events:
            if e.event_type in (EventType.LLM_REQUEST, EventType.LLM_RESPONSE,
                                EventType.TOOL_CALL, EventType.TOOL_RESULT):
                assert "round" in e.data, f"event {e.event_type} missing round"


class TestAgentRunnerCanvasState:
    @pytest.mark.asyncio
    async def test_canvas_state_in_prompt(self):
        provider = make_provider([LLMResponse(content="ok")])
        runner = AgentRunner(AgentConfig(provider=provider, registry=make_registry()))
        canvas = {"objects": [{"type": "circle", "left": 100, "top": 100, "fill": "red"}]}
        await runner.run("what's on canvas", canvas_state=canvas)

    def test_format_canvas_state_empty(self):
        runner = AgentRunner(AgentConfig(provider=make_provider([]), registry=make_registry()))
        assert runner._format_canvas_state({}) == "Empty canvas"
        assert runner._format_canvas_state({"objects": []}) == "Empty canvas"

    def test_format_canvas_state_with_objects(self):
        runner = AgentRunner(AgentConfig(provider=make_provider([]), registry=make_registry()))
        state = {"objects": [
            {"type": "circle", "left": 10, "top": 20, "fill": "red"},
            {"type": "rect", "left": 30, "top": 40, "fill": "blue"},
        ]}
        desc = runner._format_canvas_state(state)
        assert "2 objects" in desc
        assert "circle" in desc
        assert "rect" in desc


class TestAgentRunnerAssemble:
    def test_assemble_chaining(self):
        runner = AgentRunner(AgentConfig(provider=make_provider([]), registry=ToolRegistry()))
        result = runner.assemble(tools=[ALL_TOOLS[0]()])
        assert result is runner
        assert len(runner.registry) == 1


class TestAgentResponse:
    def test_to_dict(self):
        resp = AgentResponse(run_id="250101-120000-123456", content="hi", rounds=1)
        d = resp.to_dict()
        assert d["run_id"] == "250101-120000-123456"
        assert d["content"] == "hi"
        assert d["rounds"] == 1

    def test_to_dict_error(self):
        resp = AgentResponse(success=False, error="bad")
        d = resp.to_dict()
        assert d["error"] == "bad"

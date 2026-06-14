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

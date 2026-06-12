import asyncio
import pytest
from events import (
    EventBus,
    BaseEvent,
    EventType,
    SocketStartEvent,
    SocketStopEvent,
    SocketErrorEvent,
    ClientConnectEvent,
    ClientDisconnectEvent,
    ClientErrorEvent,
    LLMRequestEvent,
    LLMResponseEvent,
    LLMErrorEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolErrorEvent,
    AgentStartEvent,
    AgentStopEvent,
    AgentErrorEvent,
)


@pytest.fixture
def event_bus():
    return EventBus()


class TestEventBusBasic:
    @pytest.mark.asyncio
    async def test_register_and_dispatch(self, event_bus):
        results = []

        async def handler(e):
            results.append(e.event_type)

        event_bus.register(EventType.CLIENT_CONNECT, handler)
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))

        assert len(results) == 1
        assert results[0] == EventType.CLIENT_CONNECT

    @pytest.mark.asyncio
    async def test_multiple_handlers(self, event_bus):
        results = []

        async def handler1(e):
            results.append("h1")

        async def handler2(e):
            results.append("h2")

        event_bus.register(EventType.CLIENT_CONNECT, handler1)
        event_bus.register(EventType.CLIENT_CONNECT, handler2)
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))

        assert results == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_unregister(self, event_bus):
        results = []

        async def handler(e):
            results.append("called")

        event_bus.register(EventType.CLIENT_CONNECT, handler)
        event_bus.unregister(EventType.CLIENT_CONNECT, handler)
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))

        assert len(results) == 0


class TestEventBusPriority:
    @pytest.mark.asyncio
    async def test_priority_order(self, event_bus):
        results = []

        async def low_priority(e):
            results.append("low")

        async def high_priority(e):
            results.append("high")

        event_bus.register(EventType.CLIENT_CONNECT, low_priority, priority=1)
        event_bus.register(EventType.CLIENT_CONNECT, high_priority, priority=10)
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))

        assert results == ["high", "low"]


class TestEventBusOnce:
    @pytest.mark.asyncio
    async def test_once_handler(self, event_bus):
        results = []

        async def handler(e):
            results.append("called")

        event_bus.register(EventType.CLIENT_CONNECT, handler, once=True)
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8081"))

        assert len(results) == 1


class TestEventBusGlobal:
    @pytest.mark.asyncio
    async def test_global_handler(self, event_bus):
        results = []

        async def global_handler(e):
            results.append(e.event_type)

        event_bus.register_global(global_handler)
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))
        await event_bus.dispatch(ClientDisconnectEvent(client_addr="127.0.0.1:8080"))

        assert len(results) == 2
        assert EventType.CLIENT_CONNECT in results
        assert EventType.CLIENT_DISCONNECT in results


class TestEventBusStats:
    @pytest.mark.asyncio
    async def test_stats(self, event_bus):
        async def noop(e):
            pass

        event_bus.register(EventType.CLIENT_CONNECT, noop)
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8081"))

        stats = event_bus.get_stats()
        assert stats[EventType.CLIENT_CONNECT] == 2


class TestEventBusEnable:
    @pytest.mark.asyncio
    async def test_disable_blocks_dispatch(self, event_bus):
        results = []

        async def handler(e):
            results.append("called")

        event_bus.register(EventType.CLIENT_CONNECT, handler)
        event_bus.disable()
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_enable_restores_dispatch(self, event_bus):
        results = []

        async def handler(e):
            results.append("called")

        event_bus.register(EventType.CLIENT_CONNECT, handler)
        event_bus.disable()
        event_bus.enable()
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))

        assert len(results) == 1


class TestEventBusClear:
    @pytest.mark.asyncio
    async def test_clear_specific_type(self, event_bus):
        results = []

        async def handler(e):
            results.append("called")

        event_bus.register(EventType.CLIENT_CONNECT, handler)
        event_bus.register(EventType.CLIENT_DISCONNECT, handler)
        event_bus.clear(EventType.CLIENT_CONNECT)
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))
        await event_bus.dispatch(ClientDisconnectEvent(client_addr="127.0.0.1:8080"))

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_clear_all(self, event_bus):
        results = []

        async def handler(e):
            results.append("called")

        event_bus.register(EventType.CLIENT_CONNECT, handler)
        event_bus.register(EventType.CLIENT_DISCONNECT, handler)
        event_bus.clear()
        await event_bus.dispatch(ClientConnectEvent(client_addr="127.0.0.1:8080"))
        await event_bus.dispatch(ClientDisconnectEvent(client_addr="127.0.0.1:8080"))

        assert len(results) == 0


class TestDiagnosticEvents:
    def test_socket_start_event(self):
        event = SocketStartEvent(host="0.0.0.0", port=8765)
        assert event.event_type == EventType.SOCKET_START
        assert event.data["host"] == "0.0.0.0"
        assert event.data["port"] == 8765

    def test_socket_stop_event(self):
        event = SocketStopEvent(reason="shutdown")
        assert event.event_type == EventType.SOCKET_STOP
        assert event.data["reason"] == "shutdown"

    def test_socket_error_event(self):
        event = SocketErrorEvent(error="bind failed", host="0.0.0.0", port=8765)
        assert event.event_type == EventType.SOCKET_ERROR
        assert event.data["error"] == "bind failed"

    def test_client_connect_event(self):
        event = ClientConnectEvent(client_addr="127.0.0.1:1234", client_id="abc123")
        assert event.event_type == EventType.CLIENT_CONNECT
        assert event.data["client_addr"] == "127.0.0.1:1234"
        assert event.data["client_id"] == "abc123"

    def test_client_disconnect_event(self):
        event = ClientDisconnectEvent(
            client_addr="127.0.0.1:1234", client_id="abc123", reason="timeout"
        )
        assert event.event_type == EventType.CLIENT_DISCONNECT
        assert event.data["reason"] == "timeout"

    def test_client_error_event(self):
        event = ClientErrorEvent(
            client_addr="127.0.0.1:1234", client_id="abc123", error="read failed"
        )
        assert event.event_type == EventType.CLIENT_ERROR
        assert event.data["error"] == "read failed"

    def test_llm_request_event(self):
        event = LLMRequestEvent(model="gpt-4", message_count=5, tool_count=3)
        assert event.event_type == EventType.LLM_REQUEST
        assert event.data["model"] == "gpt-4"
        assert event.data["message_count"] == 5

    def test_llm_response_event(self):
        event = LLMResponseEvent(
            model="gpt-4", tokens_used=150, tool_calls=2, latency_ms=230.5
        )
        assert event.event_type == EventType.LLM_RESPONSE
        assert event.data["tokens_used"] == 150
        assert event.data["latency_ms"] == 230.5

    def test_llm_error_event(self):
        event = LLMErrorEvent(model="gpt-4", error="rate limit exceeded")
        assert event.event_type == EventType.LLM_ERROR
        assert event.data["error"] == "rate limit exceeded"

    def test_tool_call_event(self):
        event = ToolCallEvent(tool_name="draw_circle", arguments={"radius": 50})
        assert event.event_type == EventType.TOOL_CALL
        assert event.data["tool_name"] == "draw_circle"
        assert event.data["arguments"]["radius"] == 50

    def test_tool_result_event(self):
        event = ToolResultEvent(tool_name="draw_circle", success=True, result="ok")
        assert event.event_type == EventType.TOOL_RESULT
        assert event.data["success"] is True

    def test_tool_error_event(self):
        event = ToolErrorEvent(tool_name="draw_circle", error="invalid radius")
        assert event.event_type == EventType.TOOL_ERROR
        assert event.data["error"] == "invalid radius"

    def test_agent_start_event(self):
        event = AgentStartEvent(agent_name="DrawingAgent")
        assert event.event_type == EventType.AGENT_START
        assert event.data["agent_name"] == "DrawingAgent"

    def test_agent_stop_event(self):
        event = AgentStopEvent(agent_name="DrawingAgent", reason="shutdown")
        assert event.event_type == EventType.AGENT_STOP
        assert event.data["reason"] == "shutdown"

    def test_agent_error_event(self):
        event = AgentErrorEvent(agent_name="DrawingAgent", error="LLM timeout")
        assert event.event_type == EventType.AGENT_ERROR
        assert event.data["error"] == "LLM timeout"

import asyncio
import json
import pytest
from agent.daemon import TCPServer
from events import EventBus, EventType, ClientConnectEvent, ClientDisconnectEvent


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def tcp_server(event_bus):
    server = TCPServer(host="127.0.0.1", port=0)
    server.event_bus = event_bus
    return server


class TestTCPServerInit:
    def test_default_host_port(self):
        server = TCPServer()
        assert server.host == "127.0.0.1"
        assert server.port == 8765

    def test_custom_host_port(self):
        server = TCPServer(host="0.0.0.0", port=9999)
        assert server.host == "0.0.0.0"
        assert server.port == 9999

    def test_event_bus_initialized(self):
        server = TCPServer()
        assert isinstance(server.event_bus, EventBus)

    def test_handlers_empty(self):
        server = TCPServer()
        assert len(server._handlers) == 0


class TestTCPServerHandlerRegistration:
    @pytest.mark.asyncio
    async def test_register_async_handler(self, tcp_server):
        async def handler(payload):
            return {"ok": True}

        tcp_server.register_handler("test", handler)
        assert "test" in tcp_server._handlers

    def test_register_sync_handler_raises(self, tcp_server):
        def handler(payload):
            return {"ok": True}

        with pytest.raises(ValueError, match="must be async"):
            tcp_server.register_handler("test", handler)


class TestTCPServerIntegration:
    @pytest.mark.asyncio
    async def test_client_connect_event(self):
        received_events = []

        async def on_connect(e):
            received_events.append(e)

        server = TCPServer(host="127.0.0.1", port=0)
        server.event_bus.register(EventType.CLIENT_CONNECT, on_connect)

        async def handle_chat(payload):
            return {"code": "", "description": "ok", "tool_calls": 0}

        server.register_handler("chat", handle_chat)

        server_task = asyncio.create_task(server.start())
        await asyncio.wait_for(server._ready.wait(), timeout=5.0)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            await asyncio.sleep(0.1)

            message = json.dumps({"action": "chat", "payload": {"message": "hello"}})
            writer.write((message + "\n").encode())
            await writer.drain()

            response = await reader.readline()
            data = json.loads(response.decode())
            assert data["description"] == "ok"

            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.2)

            assert len(received_events) >= 1
            assert received_events[0].event_type == EventType.CLIENT_CONNECT
        finally:
            server.stop()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_client_disconnect_event(self):
        received_events = []

        async def on_disconnect(e):
            received_events.append(e)

        server = TCPServer(host="127.0.0.1", port=0)
        server.event_bus.register(EventType.CLIENT_DISCONNECT, on_disconnect)

        async def handle_chat(payload):
            return {"code": "", "description": "ok", "tool_calls": 0}

        server.register_handler("chat", handle_chat)

        server_task = asyncio.create_task(server.start())
        await asyncio.wait_for(server._ready.wait(), timeout=5.0)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            await asyncio.sleep(0.1)

            message = json.dumps({"action": "chat", "payload": {"message": "hello"}})
            writer.write((message + "\n").encode())
            await writer.drain()

            response = await reader.readline()
            assert response

            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.3)

            assert len(received_events) >= 1
            assert received_events[0].event_type == EventType.CLIENT_DISCONNECT
        finally:
            server.stop()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_unknown_action_error(self):
        server = TCPServer(host="127.0.0.1", port=0)

        server_task = asyncio.create_task(server.start())
        await asyncio.wait_for(server._ready.wait(), timeout=5.0)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)

            message = json.dumps({"action": "unknown", "payload": {}})
            writer.write((message + "\n").encode())
            await writer.drain()

            response = await reader.readline()
            data = json.loads(response.decode())
            assert "error" in data
            assert "Unknown action" in data["error"]

            writer.close()
            await writer.wait_closed()
        finally:
            server.stop()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

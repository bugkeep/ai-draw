import asyncio
import json
import pytest
import socket
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


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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

        port = get_free_port()
        server = TCPServer(host="127.0.0.1", port=port)
        server.event_bus.register(EventType.CLIENT_CONNECT, on_connect)

        async def handle_chat(payload):
            return {"code": "", "description": "ok", "tool_calls": 0}

        server.register_handler("chat", handle_chat)

        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
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

        port = get_free_port()
        server = TCPServer(host="127.0.0.1", port=port)
        server.event_bus.register(EventType.CLIENT_DISCONNECT, on_disconnect)

        async def handle_chat(payload):
            return {"code": "", "description": "ok", "tool_calls": 0}

        server.register_handler("chat", handle_chat)

        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
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
    async def test_invalid_json_error(self, event_bus):
        port = get_free_port()
        server = TCPServer(host="127.0.0.1", port=port)

        async def handle_chat(payload):
            return {"code": "", "description": "ok", "tool_calls": 0}

        server.register_handler("chat", handle_chat)

        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)

            writer.write(b"invalid json\n")
            await writer.drain()

            response = await reader.readline()
            data = json.loads(response.decode())
            assert "error" in data
            assert "Invalid JSON" in data["error"]

            writer.close()
            await writer.wait_closed()
        finally:
            server.stop()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_unknown_action_error(self, event_bus):
        port = get_free_port()
        server = TCPServer(host="127.0.0.1", port=port)

        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)

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

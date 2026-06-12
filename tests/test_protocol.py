import asyncio
import json
import pytest
from protocol import (
    ProtocolHandler,
    ProtocolMessage,
    ProtocolResponse,
    SessionManager,
    MessageQueue,
    HeartbeatManager,
)
from events import EventBus, EventType


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def handler(event_bus):
    return ProtocolHandler(event_bus)


@pytest.fixture
def session_manager(event_bus):
    return SessionManager(event_bus, timeout=5)


@pytest.fixture
def message_queue(event_bus):
    return MessageQueue(event_bus)


class TestProtocolMessage:
    def test_create_message(self):
        msg = ProtocolMessage(action="chat", payload={"text": "hello"})
        assert msg.action == "chat"
        assert msg.payload["text"] == "hello"
        assert msg.id

    def test_to_dict(self):
        msg = ProtocolMessage(action="chat", payload={"text": "hello"})
        d = msg.to_dict()
        assert d["action"] == "chat"
        assert d["payload"]["text"] == "hello"
        assert "id" in d

    def test_from_dict(self):
        data = {"id": "test-123", "action": "chat", "payload": {"text": "hi"}}
        msg = ProtocolMessage.from_dict(data)
        assert msg.id == "test-123"
        assert msg.action == "chat"


class TestProtocolResponse:
    def test_success(self):
        resp = ProtocolResponse.success("req-1", {"code": ""})
        assert resp.ok is True
        assert resp.data == {"code": ""}
        assert resp.id == "req-1"

    def test_error(self):
        resp = ProtocolResponse.fail("req-1", "Something failed")
        assert resp.ok is False
        assert resp.error_msg == "Something failed"

    def test_to_dict_success(self):
        resp = ProtocolResponse.success("req-1", {"code": ""})
        d = resp.to_dict()
        assert d["ok"] is True
        assert d["data"] == {"code": ""}

    def test_to_dict_error(self):
        resp = ProtocolResponse.fail("req-1", "fail")
        d = resp.to_dict()
        assert d["ok"] is False
        assert d["error"] == "fail"


class TestProtocolHandler:
    @pytest.mark.asyncio
    async def test_register_and_handle(self, handler):
        async def chat(payload):
            return {"code": "", "description": "ok"}

        handler.register("chat", chat)

        raw = json.dumps({"id": "test-1", "action": "chat", "payload": {"text": "hi"}})
        result = await handler.handle(raw)
        data = json.loads(result)

        assert data["ok"] is True
        assert data["id"] == "test-1"
        assert data["data"]["description"] == "ok"

    @pytest.mark.asyncio
    async def test_invalid_json(self, handler):
        result = await handler.handle("not json")
        data = json.loads(result)

        assert data["ok"] is False
        assert "Invalid JSON" in data["error"]

    @pytest.mark.asyncio
    async def test_missing_action(self, handler):
        raw = json.dumps({"id": "test-1", "payload": {}})
        result = await handler.handle(raw)
        data = json.loads(result)

        assert data["ok"] is False
        assert "Missing action" in data["error"]

    @pytest.mark.asyncio
    async def test_unknown_action(self, handler):
        raw = json.dumps({"id": "test-1", "action": "unknown", "payload": {}})
        result = await handler.handle(raw)
        data = json.loads(result)

        assert data["ok"] is False
        assert "Unknown action" in data["error"]

    @pytest.mark.asyncio
    async def test_handler_exception(self, handler):
        async def bad_handler(payload):
            raise ValueError("test error")

        handler.register("bad", bad_handler)

        raw = json.dumps({"id": "test-1", "action": "bad", "payload": {}})
        result = await handler.handle(raw)
        data = json.loads(result)

        assert data["ok"] is False
        assert "test error" in data["error"]

    @pytest.mark.asyncio
    async def test_middleware(self, handler):
        blocked = []

        @handler.middleware
        async def block_bad(message):
            if message.payload.get("block"):
                blocked.append(message.id)
                return False
            return True

        async def chat(payload):
            return {"ok": True}

        handler.register("chat", chat)

        raw = json.dumps({"id": "test-1", "action": "chat", "payload": {"block": True}})
        result = await handler.handle(raw)
        data = json.loads(result)

        assert data["ok"] is False
        assert "Blocked by middleware" in data["error"]
        assert "test-1" in blocked

    @pytest.mark.asyncio
    async def test_execute(self, handler):
        async def chat(payload):
            return {"code": ""}

        handler.register("chat", chat)

        msg = ProtocolMessage(action="chat", payload={"text": "hi"})
        resp = await handler.execute(msg)

        assert resp.ok is True
        assert resp.data == {"code": ""}


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_create_session(self, session_manager):
        session = await session_manager.create("client-1")
        assert session.session_id
        assert session.client_id == "client-1"

    @pytest.mark.asyncio
    async def test_get_session(self, session_manager):
        session = await session_manager.create("client-1")
        found = await session_manager.get(session.session_id)
        assert found is not None
        assert found.session_id == session.session_id

    @pytest.mark.asyncio
    async def test_destroy_session(self, session_manager):
        session = await session_manager.create("client-1")
        result = await session_manager.destroy(session.session_id)
        assert result is True

        found = await session_manager.get(session.session_id)
        assert found is None

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, session_manager):
        session_manager.timeout = 0
        await session_manager.create("client-1")
        await session_manager.create("client-2")

        await asyncio.sleep(0.1)
        count = await session_manager.cleanup_expired()
        assert count == 2

    @pytest.mark.asyncio
    async def test_active_count(self, session_manager):
        await session_manager.create("client-1")
        await session_manager.create("client-2")
        assert session_manager.active_count == 2


class TestMessageQueue:
    @pytest.mark.asyncio
    async def test_enqueue(self, message_queue):
        msg = ProtocolMessage(action="chat", payload={})
        result = await message_queue.enqueue(msg, "client-1")
        assert result is True
        assert message_queue.size == 1

    @pytest.mark.asyncio
    async def test_process_next(self, message_queue):
        async def handler(msg):
            return ProtocolResponse.success(msg.id, {"processed": True})

        message_queue.set_handler(handler)

        msg = ProtocolMessage(action="chat", payload={})
        await message_queue.enqueue(msg, "client-1")

        result = await message_queue.process_next()
        assert result.ok is True
        assert message_queue.is_empty

    @pytest.mark.asyncio
    async def test_retry_on_failure(self, message_queue):
        attempt = 0

        async def handler(msg):
            nonlocal attempt
            attempt += 1
            if attempt < 2:
                raise ValueError("temporary failure")
            return ProtocolResponse.success(msg.id, {"ok": True})

        message_queue.set_handler(handler)

        msg = ProtocolMessage(action="chat", payload={})
        await message_queue.enqueue(msg, "client-1", max_retries=3)

        result = await message_queue.process_next()
        assert result.ok is False

        result = await message_queue.process_next()
        assert result.ok is True


class TestHeartbeatManager:
    @pytest.mark.asyncio
    async def test_record_and_check(self, session_manager, event_bus):
        hb = HeartbeatManager(session_manager, event_bus, timeout=1)
        await hb.record("client-1")
        assert hb.is_alive("client-1")

    @pytest.mark.asyncio
    async def test_timeout(self, session_manager, event_bus):
        hb = HeartbeatManager(session_manager, event_bus, timeout=0)
        await hb.record("client-1")
        await asyncio.sleep(0.1)
        assert not hb.is_alive("client-1")

    @pytest.mark.asyncio
    async def test_check_dead(self, session_manager, event_bus):
        hb = HeartbeatManager(session_manager, event_bus, timeout=0)
        await hb.record("client-1")
        await asyncio.sleep(0.1)
        dead = await hb.check_dead()
        assert "client-1" in dead

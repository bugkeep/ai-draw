import os
import json
import asyncio
import shutil
import pytest
from unittest.mock import MagicMock
from events import (
    EventBus, BaseEvent, EventType, EventBroadcaster, Subscription,
    format_event_push,
)
from core.app import events_file, _replay_events, JsonlRecorder
from protocol.models import EventPushEnvelope


# ── EventPushEnvelope ───────────────────────────────────────────────

class TestEventPushEnvelope:
    def test_to_dict_has_correct_fields(self):
        env = EventPushEnvelope(
            topic="llm.request",
            event_type="llm_request",
            data={"model": "gpt-4"},
            timestamp=100.0,
            run_id="run-abc",
        )
        d = env.to_dict()
        assert d["type"] == "event_push"
        assert d["topic"] == "llm.request"
        assert d["event_type"] == "llm_request"
        assert d["data"]["model"] == "gpt-4"
        assert d["timestamp"] == 100.0
        assert d["run_id"] == "run-abc"

    def test_default_run_id_empty(self):
        env = EventPushEnvelope(
            topic="tool.call", event_type="tool_call",
            data={}, timestamp=200.0,
        )
        d = env.to_dict()
        assert d["run_id"] == ""

    def test_json_roundtrip(self):
        env = EventPushEnvelope(
            topic="agent.start", event_type="agent_start",
            data={"agent": "main"}, timestamp=300.0, run_id="r1",
        )
        restored = json.loads(json.dumps(env.to_dict()))
        assert restored["type"] == "event_push"
        assert restored["topic"] == "agent.start"


# ── format_event_push ───────────────────────────────────────────────

class TestFormatEventPush:
    def test_from_base_event(self):
        e = BaseEvent(EventType.LLM_REQUEST, {"model": "gpt-4"}, run_id="r1")
        s = format_event_push(e)
        d = json.loads(s)
        assert d["type"] == "event_push"
        assert d["topic"] == "llm.request"
        assert d["event_type"] == "llm_request"
        assert d["run_id"] == "r1"

    def test_topic_without_run_id(self):
        e = BaseEvent(EventType.TOOL_CALL, {"name": "draw"})
        s = format_event_push(e)
        d = json.loads(s)
        assert d["topic"] == "tool.call"
        assert d["run_id"] == ""

    def test_topic_override(self):
        e = BaseEvent(EventType.SYSTEM, {}, topic="custom.topic")
        d = json.loads(format_event_push(e))
        assert d["topic"] == "custom.topic"

    def test_run_id_from_data_fallback(self):
        e = BaseEvent(EventType.AGENT_START, {"run_id": "r-from-data"})
        d = json.loads(format_event_push(e))
        assert d["run_id"] == "r-from-data"


# ── Subscription.matches ────────────────────────────────────────────

class TestSubscriptionMatches:
    def test_wildcard_matches_all(self):
        sub = Subscription(sub_id="s1", topics=["*"], scope="global")
        assert sub.matches(BaseEvent(EventType.LLM_REQUEST))
        assert sub.matches(BaseEvent(EventType.TOOL_CALL))
        assert sub.matches(BaseEvent(EventType.SOCKET_START))

    def test_topic_filter(self):
        sub = Subscription(sub_id="s1", topics=["llm.*", "tool.*"])
        assert sub.matches(BaseEvent(EventType.LLM_REQUEST))    # llm.request
        assert sub.matches(BaseEvent(EventType.TOOL_CALL))       # tool.call
        assert not sub.matches(BaseEvent(EventType.SOCKET_START))  # socket.start

    def test_exact_topic(self):
        sub = Subscription(sub_id="s1", topics=["llm.request"])
        assert sub.matches(BaseEvent(EventType.LLM_REQUEST))
        assert not sub.matches(BaseEvent(EventType.TOOL_CALL))

    def test_multiple_patterns(self):
        sub = Subscription(sub_id="s1", topics=["llm.*", "agent.*"])
        assert sub.matches(BaseEvent(EventType.LLM_REQUEST))
        assert sub.matches(BaseEvent(EventType.AGENT_START))
        assert not sub.matches(BaseEvent(EventType.TOOL_CALL))

    def test_global_scope_always_matches(self):
        sub = Subscription(sub_id="s1", topics=["*"], scope="global")
        assert sub.matches(BaseEvent(EventType.SYSTEM, {}))

    def test_run_scope_match(self):
        sub = Subscription(sub_id="s1", topics=["*"], scope="run:abc123")
        e = BaseEvent(EventType.TOOL_CALL, {"run_id": "abc123"})
        assert sub.matches(e)

    def test_run_scope_mismatch(self):
        sub = Subscription(sub_id="s1", topics=["*"], scope="run:abc123")
        e = BaseEvent(EventType.TOOL_CALL, {"run_id": "other"})
        assert not sub.matches(e)

    def test_run_scope_prefers_event_run_id(self):
        sub = Subscription(sub_id="s1", topics=["*"], scope="run:abc123")
        e = BaseEvent(EventType.TOOL_CALL, {}, run_id="abc123")
        assert sub.matches(e)

    def test_run_scope_no_run_id(self):
        sub = Subscription(sub_id="s1", topics=["*"], scope="run:abc123")
        e = BaseEvent(EventType.SYSTEM, {})
        assert not sub.matches(e)


# ── Subscription.send ───────────────────────────────────────────────

class TestSubscriptionSend:
    @pytest.mark.asyncio
    async def test_send_writer(self):
        buf = bytearray()
        class FakeWriter:
            def write(self, b): buf.extend(b)
            async def drain(self): pass

        sub = Subscription(sub_id="s1", writer=FakeWriter())
        await sub.send('{"hello":"world"}')
        assert b"hello" in buf

    @pytest.mark.asyncio
    async def test_send_appends_newline(self):
        buf = bytearray()
        class FakeWriter:
            def write(self, b): buf.extend(b)
            async def drain(self): pass

        sub = Subscription(sub_id="s1", writer=FakeWriter())
        await sub.send('{"x":1}')
        assert buf.endswith(b"\n"), f"expected newline, got {buf}"


# ── JsonlRecorder ───────────────────────────────────────────────────

class TestJsonlRecorder:
    @pytest.mark.asyncio
    async def test_records_events_with_run_id(self):
        bus = EventBus()
        rec = JsonlRecorder(bus)
        await bus.dispatch(
            BaseEvent(EventType.LLM_REQUEST, {"model": "gpt-4"}, run_id="test-rec")
        )
        fp = events_file("test-rec")
        assert os.path.isfile(fp)
        with open(fp) as f:
            line = f.readline().strip()
        d = json.loads(line)
        assert d["type"] == "event_push"
        assert d["topic"] == "llm.request"
        shutil.rmtree(os.path.dirname(fp))

    @pytest.mark.asyncio
    async def test_ignores_events_without_run_id(self):
        bus = EventBus()
        rec = JsonlRecorder(bus)
        await bus.dispatch(BaseEvent(EventType.SYSTEM, {"no": "run_id"}))
        # No runs dir should be created for a nameless run
        assert not os.path.isfile(events_file(""))

    @pytest.mark.asyncio
    async def test_records_run_id_from_data_fallback(self):
        bus = EventBus()
        rec = JsonlRecorder(bus)
        await bus.dispatch(
            BaseEvent(EventType.AGENT_START, {"run_id": "r-from-data"})
        )
        fp = events_file("r-from-data")
        assert os.path.isfile(fp)
        shutil.rmtree(os.path.dirname(fp))


# ── _replay_events ──────────────────────────────────────────────────

class TestReplayEvents:
    @pytest.mark.asyncio
    async def test_replay_non_existent_returns_zero(self):
        writer = MagicMock()
        c = await _replay_events("no-such-run", writer)
        assert c == 0
        writer.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_replay(self):
        bus = EventBus()
        rec = JsonlRecorder(bus)
        await bus.dispatch(
            BaseEvent(EventType.LLM_REQUEST, {}, run_id="test-rep")
        )
        await bus.dispatch(
            BaseEvent(EventType.TOOL_CALL, {}, run_id="test-rep")
        )

        buf = bytearray()
        class FakeWriter:
            def write(self, b): buf.extend(b)
            async def drain(self): pass

        c = await _replay_events("test-rep", FakeWriter())
        assert c == 2
        lines = [l for l in buf.decode().split("\n") if l]
        assert len(lines) == 2

        fp = events_file("test-rep")
        shutil.rmtree(os.path.dirname(fp))

    @pytest.mark.asyncio
    async def test_replay_empty_lines_skipped(self):
        run_id = "test-empty"
        fp = events_file(run_id)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w") as f:
            f.write("\n\n\n")

        buf = bytearray()
        class FakeWriter:
            def write(self, b): buf.extend(b)
            async def drain(self): pass

        c = await _replay_events(run_id, FakeWriter())
        assert c == 0
        shutil.rmtree(os.path.dirname(fp))


# ── EventBroadcaster subscription flow ──────────────────────────────

class TestEventBroadcasterSubscriptions:
    @pytest.mark.asyncio
    async def test_subscription_filters_by_topic(self):
        bus = EventBus()
        bc = EventBroadcaster(bus)
        received = []
        class FakeWriter:
            def write(self, b): received.append(b)
            async def drain(self): pass

        sub = Subscription(sub_id="s1", writer=FakeWriter(), topics=["llm.*"])
        bc.subscribe(sub)

        await bus.dispatch(BaseEvent(EventType.LLM_REQUEST, {}))   # matches
        await bus.dispatch(BaseEvent(EventType.TOOL_CALL, {}))      # no match

        assert len(received) == 1
        d = json.loads(received[0].decode())
        assert d["topic"] == "llm.request"

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_writer(self):
        bus = EventBus()
        bc = EventBroadcaster(bus)
        received = []
        class FakeWriter:
            def write(self, b): received.append(b)
            async def drain(self): pass

        fw = FakeWriter()
        sub = Subscription(sub_id="s1", writer=fw, topics=["*"])
        bc.subscribe(sub)

        await bus.dispatch(BaseEvent(EventType.SYSTEM, {}))
        assert len(received) == 1

        bc.unsubscribe(fw)
        await bus.dispatch(BaseEvent(EventType.SYSTEM, {}))
        assert len(received) == 1  # no second event

    @pytest.mark.asyncio
    async def test_dead_writer_cleaned_up(self):
        bus = EventBus()
        bc = EventBroadcaster(bus)
        dead = []
        class DeadWriter:
            def write(self, b): raise ConnectionError("gone")
            async def drain(self): raise ConnectionError("gone")

        sub = Subscription(sub_id="dead", writer=DeadWriter(), topics=["*"])
        bc.subscribe(sub)

        await bus.dispatch(BaseEvent(EventType.SYSTEM, {}))
        assert len(bc._subscriptions) == 0  # autoclean

    @pytest.mark.asyncio
    async def test_run_scope_subscription(self):
        bus = EventBus()
        bc = EventBroadcaster(bus)
        received = []
        class FakeWriter:
            def write(self, b): received.append(b)
            async def drain(self): pass

        sub = Subscription(
            sub_id="s1", writer=FakeWriter(),
            topics=["*"], scope="run:abc123",
        )
        bc.subscribe(sub)

        await bus.dispatch(BaseEvent(EventType.LLM_REQUEST, {"run_id": "abc123"}))
        await bus.dispatch(BaseEvent(EventType.LLM_REQUEST, {"run_id": "other"}))
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_multiple_subscriptions(self):
        bus = EventBus()
        bc = EventBroadcaster(bus)
        buf1, buf2 = bytearray(), bytearray()
        class W1:
            def write(self, b): buf1.extend(b)
            async def drain(self): pass
        class W2:
            def write(self, b): buf2.extend(b)
            async def drain(self): pass

        bc.subscribe(Subscription(sub_id="a", writer=W1(), topics=["llm.*"]))
        bc.subscribe(Subscription(sub_id="b", writer=W2(), topics=["tool.*"]))

        await bus.dispatch(BaseEvent(EventType.LLM_REQUEST, {}))
        assert len(buf1) > 0
        assert len(buf2) == 0

        await bus.dispatch(BaseEvent(EventType.TOOL_CALL, {}))
        assert len(buf2) > 0


# ── BaseEvent.get_topic ─────────────────────────────────────────────

class TestBaseEventTopic:
    def test_llm_request_topic(self):
        assert BaseEvent(EventType.LLM_REQUEST).get_topic() == "llm.request"

    def test_tool_call_topic(self):
        assert BaseEvent(EventType.TOOL_CALL).get_topic() == "tool.call"

    def test_agent_start_topic(self):
        assert BaseEvent(EventType.AGENT_START).get_topic() == "agent.start"

    def test_socket_start_topic(self):
        assert BaseEvent(EventType.SOCKET_START).get_topic() == "socket.start"

    def test_explicit_topic_override(self):
        e = BaseEvent(EventType.SYSTEM, {}, topic="custom.topic")
        assert e.get_topic() == "custom.topic"

    def test_run_id_field(self):
        e = BaseEvent(EventType.TOOL_CALL, {}, run_id="run-001")
        assert e.run_id == "run-001"

    def test_run_id_default_empty(self):
        e = BaseEvent(EventType.SYSTEM)
        assert e.run_id == ""


# ── TCP event_subscribe integration ─────────────────────────────────

class TestTcpEventSubscribe:
    @pytest.mark.asyncio
    async def test_event_subscribe_global_scope_no_replay(self):
        from agent.daemon import TCPServer
        bus = EventBus()
        bc = EventBroadcaster(bus)
        server = TCPServer(host="127.0.0.1", port=0, broadcaster=bc, event_bus=bus)

        async def handle_chat(payload):
            return {"code": "", "description": "ok", "tool_calls": 0}
        server.register_handler("chat", handle_chat)

        server_task = asyncio.create_task(server.start())
        await asyncio.wait_for(server._ready.wait(), timeout=5.0)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            await asyncio.sleep(0.1)

            msg = json.dumps({
                "action": "event_subscribe",
                "payload": {"topics": ["llm.*"], "scope": "global"},
            }) + "\n"
            writer.write(msg.encode())
            await writer.drain()

            resp = await reader.readline()
            data = json.loads(resp.decode())
            assert data["replayed_count"] == 0

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
    async def test_event_subscribe_then_live_events(self):
        """After subscribing, live events go to the writer."""
        from agent.daemon import TCPServer
        bus = EventBus()
        bc = EventBroadcaster(bus)
        server = TCPServer(host="127.0.0.1", port=0, broadcaster=bc, event_bus=bus)

        async def handle_chat(payload):
            return {"code": "", "description": "ok", "tool_calls": 0}
        server.register_handler("chat", handle_chat)

        server_task = asyncio.create_task(server.start())
        await asyncio.wait_for(server._ready.wait(), timeout=5.0)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            await asyncio.sleep(0.1)

            # Subscribe
            msg = json.dumps({
                "action": "event_subscribe",
                "payload": {"topics": ["llm.*"], "scope": "global"},
            }) + "\n"
            writer.write(msg.encode())
            await writer.drain()

            resp = await reader.readline()
            data = json.loads(resp.decode())
            assert data["replayed_count"] == 0

            # Now fire a live event
            await bus.dispatch(BaseEvent(EventType.LLM_REQUEST, {}))
            await asyncio.sleep(0.1)

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
    async def test_chat_action_still_works(self):
        """Non-subscribe actions work as before."""
        from agent.daemon import TCPServer
        bus = EventBus()
        bc = EventBroadcaster(bus)
        server = TCPServer(host="127.0.0.1", port=0, broadcaster=bc, event_bus=bus)

        async def handle_chat(payload):
            return {"response": f"got: {payload.get('message', '')}"}
        server.register_handler("chat", handle_chat)

        server_task = asyncio.create_task(server.start())
        await asyncio.wait_for(server._ready.wait(), timeout=5.0)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            await asyncio.sleep(0.1)

            msg = json.dumps({
                "action": "chat",
                "payload": {"message": "hello"},
            }) + "\n"
            writer.write(msg.encode())
            await writer.drain()

            resp = await reader.readline()
            data = json.loads(resp.decode())
            assert data["response"] == "got: hello"

            writer.close()
            await writer.wait_closed()
        finally:
            server.stop()
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass


# ── Existing e2e HTTP tests still pass ──────────────────────────────

class TestExistingHttpEndpoints:
    """Smoke test: create_app still works."""
    def test_create_app(self):
        from server.app import create_app
        app = create_app()
        assert app.title == "AI Voice Draw"

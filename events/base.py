import json
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable
import asyncio
import time
from collections import defaultdict


class EventType(Enum):
    # 业务事件
    VOICE_RECEIVED = "voice_received"
    TOOL_EXECUTED = "tool_executed"
    ERROR = "error"
    SYSTEM = "system"

    # 诊断事件 - TCP/Socket
    SOCKET_START = "socket_start"
    SOCKET_STOP = "socket_stop"
    SOCKET_ERROR = "socket_error"
    CLIENT_CONNECT = "client_connect"
    CLIENT_DISCONNECT = "client_disconnect"
    CLIENT_ERROR = "client_error"

    # 诊断事件 - LLM
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    LLM_ERROR = "llm_error"
    LLM_RETRY = "llm_retry"

    # 诊断事件 - Tool
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    TOOL_BLOCKED = "tool_blocked"        # 权限审批拒绝
    TOOL_VALIDATION_ERROR = "tool_validation_error"  # 参数校验失败

    # 诊断事件 - Agent
    AGENT_START = "agent_start"
    AGENT_STOP = "agent_stop"
    AGENT_ERROR = "agent_error"

    # 会话事件
    SESSION_CREATED = "session_created"

    # 权限事件
    PERMISSION_REQUESTED = "permission_requested"
    PERMISSION_RESPONDED = "permission_responded"


@dataclass
class BaseEvent:
    event_type: EventType = EventType.SYSTEM
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    topic: str = ""
    run_id: str = ""

    def get_topic(self) -> str:
        """Derive dot-separated topic from event_type if not explicitly set.

        e.g. EventType.LLM_REQUEST → "llm.request"
        """
        return self.topic or self.event_type.name.lower().replace("_", ".")


@dataclass
class HandlerEntry:
    handler: Callable
    priority: int = 0
    once: bool = False


class EventBus:
    def __init__(self):
        self._handlers: dict[EventType, list[HandlerEntry]] = defaultdict(list)
        self._global_handlers: list[HandlerEntry] = []
        self._stats: dict[EventType, int] = defaultdict(int)
        self._enabled = True

    def register(
        self, event_type: EventType, handler: Callable, priority: int = 0, once: bool = False
    ):
        entry = HandlerEntry(handler=handler, priority=priority, once=once)
        self._handlers[event_type].append(entry)
        self._handlers[event_type].sort(key=lambda e: e.priority, reverse=True)

    def register_global(self, handler: Callable, priority: int = 0, once: bool = False):
        entry = HandlerEntry(handler=handler, priority=priority, once=once)
        self._global_handlers.append(entry)
        self._global_handlers.sort(key=lambda e: e.priority, reverse=True)

    def unregister(self, event_type: EventType, handler: Callable):
        self._handlers[event_type] = [
            e for e in self._handlers[event_type] if e.handler != handler
        ]

    def unregister_global(self, handler: Callable):
        self._global_handlers = [e for e in self._global_handlers if e.handler != handler]

    def clear(self, event_type: EventType | None = None):
        if event_type:
            self._handlers[event_type].clear()
        else:
            self._handlers.clear()
            self._global_handlers.clear()

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def get_stats(self) -> dict[str, int]:
        return dict(self._stats)

    async def dispatch(self, event: BaseEvent):
        if not self._enabled:
            return

        self._stats[event.event_type] += 1

        handlers = self._global_handlers + self._handlers.get(event.event_type, [])
        handlers.sort(key=lambda e: e.priority, reverse=True)

        once_entries: list[HandlerEntry] = []

        for entry in handlers:
            try:
                if asyncio.iscoroutinefunction(entry.handler):
                    await entry.handler(event)
                else:
                    entry.handler(event)
            except Exception as e:
                print(f"EventBus handler error: {e}")

            if entry.once:
                once_entries.append(entry)

        for entry in once_entries:
            if entry in self._global_handlers:
                self._global_handlers.remove(entry)
            elif entry in self._handlers.get(event.event_type, []):
                self._handlers[event.event_type].remove(entry)


def format_event_push(event: BaseEvent) -> str:
    """Serialize an event into the standard EventPushEnvelope wire format."""
    return json.dumps({
        "type": "event_push",
        "topic": event.get_topic(),
        "event_type": event.event_type.value,
        "data": event.data,
        "timestamp": event.timestamp,
        "run_id": event.run_id or event.data.get("run_id", ""),
    }, ensure_ascii=False)

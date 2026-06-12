from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable
import asyncio
import time


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

    # 诊断事件 - Tool
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"

    # 诊断事件 - Agent
    AGENT_START = "agent_start"
    AGENT_STOP = "agent_stop"
    AGENT_ERROR = "agent_error"


@dataclass
class BaseEvent:
    event_type: EventType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EventBus:
    def __init__(self):
        self._handlers: dict[EventType, list[Callable]] = {}

    def register(self, event_type: EventType, handler: Callable):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    async def dispatch(self, event: BaseEvent):
        for handler in self._handlers.get(event.event_type, []):
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)

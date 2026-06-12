from dataclasses import dataclass, field
from typing import Any
from .base import BaseEvent, EventType


@dataclass
class SocketStartEvent(BaseEvent):
    host: str = ""
    port: int = 0

    def __post_init__(self):
        self.event_type = EventType.SOCKET_START
        if not self.data:
            self.data = {"host": self.host, "port": self.port}


@dataclass
class SocketStopEvent(BaseEvent):
    reason: str = ""

    def __post_init__(self):
        self.event_type = EventType.SOCKET_STOP
        if not self.data:
            self.data = {"reason": self.reason}


@dataclass
class SocketErrorEvent(BaseEvent):
    error: str = ""
    host: str = ""
    port: int = 0

    def __post_init__(self):
        self.event_type = EventType.SOCKET_ERROR
        if not self.data:
            self.data = {"error": self.error, "host": self.host, "port": self.port}


@dataclass
class ClientConnectEvent(BaseEvent):
    client_addr: str = ""
    client_id: str = ""

    def __post_init__(self):
        self.event_type = EventType.CLIENT_CONNECT
        if not self.data:
            self.data = {"client_addr": self.client_addr, "client_id": self.client_id}


@dataclass
class ClientDisconnectEvent(BaseEvent):
    client_addr: str = ""
    client_id: str = ""
    reason: str = ""

    def __post_init__(self):
        self.event_type = EventType.CLIENT_DISCONNECT
        if not self.data:
            self.data = {
                "client_addr": self.client_addr,
                "client_id": self.client_id,
                "reason": self.reason,
            }


@dataclass
class ClientErrorEvent(BaseEvent):
    client_addr: str = ""
    client_id: str = ""
    error: str = ""

    def __post_init__(self):
        self.event_type = EventType.CLIENT_ERROR
        if not self.data:
            self.data = {
                "client_addr": self.client_addr,
                "client_id": self.client_id,
                "error": self.error,
            }


@dataclass
class LLMRequestEvent(BaseEvent):
    model: str = ""
    message_count: int = 0
    tool_count: int = 0

    def __post_init__(self):
        self.event_type = EventType.LLM_REQUEST
        if not self.data:
            self.data = {
                "model": self.model,
                "message_count": self.message_count,
                "tool_count": self.tool_count,
            }


@dataclass
class LLMResponseEvent(BaseEvent):
    model: str = ""
    tokens_used: int = 0
    tool_calls: int = 0
    latency_ms: float = 0

    def __post_init__(self):
        self.event_type = EventType.LLM_RESPONSE
        if not self.data:
            self.data = {
                "model": self.model,
                "tokens_used": self.tokens_used,
                "tool_calls": self.tool_calls,
                "latency_ms": self.latency_ms,
            }


@dataclass
class LLMErrorEvent(BaseEvent):
    model: str = ""
    error: str = ""

    def __post_init__(self):
        self.event_type = EventType.LLM_ERROR
        if not self.data:
            self.data = {"model": self.model, "error": self.error}


@dataclass
class ToolCallEvent(BaseEvent):
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)

    def __post_init__(self):
        self.event_type = EventType.TOOL_CALL
        if not self.data:
            self.data = {"tool_name": self.tool_name, "arguments": self.arguments}


@dataclass
class ToolResultEvent(BaseEvent):
    tool_name: str = ""
    success: bool = True
    result: Any = None

    def __post_init__(self):
        self.event_type = EventType.TOOL_RESULT
        if not self.data:
            self.data = {
                "tool_name": self.tool_name,
                "success": self.success,
                "result": str(self.result) if self.result else None,
            }


@dataclass
class ToolErrorEvent(BaseEvent):
    tool_name: str = ""
    error: str = ""

    def __post_init__(self):
        self.event_type = EventType.TOOL_ERROR
        if not self.data:
            self.data = {"tool_name": self.tool_name, "error": self.error}


@dataclass
class AgentStartEvent(BaseEvent):
    agent_name: str = ""

    def __post_init__(self):
        self.event_type = EventType.AGENT_START
        if not self.data:
            self.data = {"agent_name": self.agent_name}


@dataclass
class AgentStopEvent(BaseEvent):
    agent_name: str = ""
    reason: str = ""

    def __post_init__(self):
        self.event_type = EventType.AGENT_STOP
        if not self.data:
            self.data = {"agent_name": self.agent_name, "reason": self.reason}


@dataclass
class AgentErrorEvent(BaseEvent):
    agent_name: str = ""
    error: str = ""

    def __post_init__(self):
        self.event_type = EventType.AGENT_ERROR
        if not self.data:
            self.data = {"agent_name": self.agent_name, "error": self.error}

from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable
import asyncio


class EventType(Enum):
    VOICE_RECEIVED = "voice_received"
    TOOL_EXECUTED = "tool_executed"
    ERROR = "error"
    SYSTEM = "system"


@dataclass
class BaseEvent:
    event_type: EventType
    data: dict = field(default_factory=dict)


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

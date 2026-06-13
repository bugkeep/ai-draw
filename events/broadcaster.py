import json
import asyncio
from typing import Any
from fastapi import WebSocket
from .base import EventBus, BaseEvent


class EventBroadcaster:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._clients: dict[str, WebSocket] = {}
        self._queue: asyncio.Queue | None = None
        event_bus.register_global(self._on_event)

    async def connect(self, ws: WebSocket, client_id: str):
        await ws.accept()
        self._clients[client_id] = ws

    def disconnect(self, client_id: str):
        self._clients.pop(client_id, None)

    async def _on_event(self, event: BaseEvent):
        message = json.dumps({
            "type": "event",
            "event_type": event.event_type.value,
            "data": event.data,
            "timestamp": event.timestamp,
        }, ensure_ascii=False)
        dead = []
        for cid, ws in self._clients.items():
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(cid)
        for cid in dead:
            self._clients.pop(cid, None)

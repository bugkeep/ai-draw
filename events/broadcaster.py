from __future__ import annotations
import json
import asyncio
from typing import Any, TYPE_CHECKING
from fastapi import WebSocket
from .base import EventBus, BaseEvent, format_event_push
from .subscription import Subscription

if TYPE_CHECKING:
    from traces.recorder import DaemonTracer


class EventBroadcaster:
    def __init__(self, event_bus: EventBus, tracer: DaemonTracer | None = None):
        self.event_bus = event_bus
        self.tracer = tracer
        self._ws_clients: dict[str, WebSocket] = {}
        self._subscriptions: dict[str, Subscription] = {}
        event_bus.register_global(self._on_event)

    async def connect(self, ws: WebSocket, client_id: str):
        await ws.accept()
        self._ws_clients[client_id] = ws

    def disconnect(self, client_id: str):
        self._ws_clients.pop(client_id, None)

    def subscribe(self, sub: Subscription):
        """Register a subscription to receive broadcast events."""
        self._subscriptions[sub.sub_id] = sub

    def unsubscribe(self, writer: asyncio.StreamWriter):
        """Remove all subscriptions for a given TCP writer immediately."""
        dead = [sid for sid, sub in self._subscriptions.items() if sub.writer is writer]
        for sid in dead:
            self._subscriptions.pop(sid, None)

    async def _on_event(self, event: BaseEvent):
        message = format_event_push(event)

        dead_ws = []
        for cid, ws in self._ws_clients.items():
            try:
                await ws.send_text(message)
            except Exception:
                dead_ws.append(cid)
        for cid in dead_ws:
            self._ws_clients.pop(cid, None)

        dead_subs = []
        for sid, sub in self._subscriptions.items():
            if not sub.matches(event):
                continue
            try:
                await sub.send(message)
                if self.tracer:
                    self.tracer.on_ipc_push(sid, event.event_type.value,
                                            run_id=event.run_id or "")
            except Exception:
                dead_subs.append(sid)
        for sid in dead_subs:
            self._subscriptions.pop(sid, None)

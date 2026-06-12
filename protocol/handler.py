import json
import inspect
from typing import Callable, Any
from .models import ProtocolMessage, ProtocolResponse
from events import EventBus, BaseEvent, EventType


class ProtocolHandler:
    def __init__(self, event_bus: EventBus | None = None):
        self.event_bus = event_bus or EventBus()
        self._handlers: dict[str, Callable] = {}
        self._middleware: list[Callable] = []

    def register(self, action: str, handler: Callable):
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"Handler for '{action}' must be async")
        self._handlers[action] = handler

    def middleware(self, handler: Callable):
        if not inspect.iscoroutinefunction(handler):
            raise ValueError("Middleware must be async")
        self._middleware.append(handler)
        return handler

    def parse(self, raw: str) -> ProtocolMessage | None:
        try:
            data = json.loads(raw.strip())
            return ProtocolMessage.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    async def validate(self, message: ProtocolMessage) -> str | None:
        if not message.action:
            return "Missing action"
        if not isinstance(message.payload, dict):
            return "Invalid payload"
        return None

    async def handle(self, raw: str) -> str:
        message = self.parse(raw)
        if not message:
            return json.dumps(
                ProtocolResponse.fail("", "Invalid JSON").to_dict()
            )

        error = await self.validate(message)
        if error:
            return json.dumps(
                ProtocolResponse.fail(message.id, error).to_dict()
            )

        for mw in self._middleware:
            result = await mw(message)
            if result is False:
                return json.dumps(
                    ProtocolResponse.fail(message.id, "Blocked by middleware").to_dict()
                )
            if isinstance(result, ProtocolResponse):
                return json.dumps(result.to_dict())

        await self.event_bus.dispatch(
            BaseEvent(EventType.VOICE_RECEIVED, message.payload)
        )

        if message.action in self._handlers:
            try:
                result = await self._handlers[message.action](message.payload)
                return json.dumps(
                    ProtocolResponse.success(
                        message.id, result, message.session_id
                    ).to_dict()
                )
            except Exception as e:
                return json.dumps(
                    ProtocolResponse.fail(
                        message.id, str(e), message.session_id
                    ).to_dict()
                )
        else:
            return json.dumps(
                ProtocolResponse.fail(
                    message.id, f"Unknown action: {message.action}"
                ).to_dict()
            )

    async def execute(self, message: ProtocolMessage) -> ProtocolResponse:
        error = await self.validate(message)
        if error:
            return ProtocolResponse.fail(message.id, error)

        for mw in self._middleware:
            result = await mw(message)
            if result is False:
                return ProtocolResponse.fail(message.id, "Blocked by middleware")
            if isinstance(result, ProtocolResponse):
                return result

        if message.action in self._handlers:
            try:
                data = await self._handlers[message.action](message.payload)
                return ProtocolResponse.success(
                    message.id, data, message.session_id
                )
            except Exception as e:
                return ProtocolResponse.fail(
                    message.id, str(e), message.session_id
                )
        else:
            return ProtocolResponse.fail(
                message.id, f"Unknown action: {message.action}"
            )

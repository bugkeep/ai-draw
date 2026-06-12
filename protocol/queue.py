import asyncio
import time
from .models import ProtocolMessage, QueuedMessage, ProtocolResponse
from events import EventBus, EventType


class MessageQueue:
    def __init__(self, event_bus: EventBus | None = None, max_size: int = 1000):
        self.event_bus = event_bus or EventBus()
        self.max_size = max_size
        self._queue: asyncio.Queue[QueuedMessage] = asyncio.Queue(maxsize=max_size)
        self._processing = False
        self._handler = None

    def set_handler(self, handler):
        self._handler = handler

    async def enqueue(
        self, message: ProtocolMessage, client_id: str = "", max_retries: int = 3
    ) -> bool:
        if self._queue.qsize() >= self.max_size:
            return False

        queued = QueuedMessage(
            message=message, client_id=client_id, max_retries=max_retries
        )
        await self._queue.put(queued)
        return True

    async def process_next(self) -> ProtocolResponse | None:
        if self._queue.empty():
            return None

        queued = await self._queue.get()

        if not self._handler:
            return ProtocolResponse.fail(
                queued.message.id, "No handler configured"
            )

        try:
            result = self._handler(queued.message)
            if asyncio.iscoroutine(result):
                result = await result
            self._queue.task_done()
            return result
        except Exception as e:
            if queued.can_retry():
                queued.retries += 1
                await self._queue.put(queued)
            return ProtocolResponse.fail(queued.message.id, str(e))

    async def process_all(self) -> list[ProtocolResponse]:
        results = []
        while not self._queue.empty():
            result = await self.process_next()
            if result:
                results.append(result)
        return results

    async def start_processing(self, interval: float = 0.1):
        self._processing = True
        while self._processing:
            if not self._queue.empty():
                await self.process_next()
            await asyncio.sleep(interval)

    def stop_processing(self):
        self._processing = False

    @property
    def size(self) -> int:
        return self._queue.qsize()

    @property
    def is_empty(self) -> bool:
        return self._queue.empty()

    def clear(self):
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

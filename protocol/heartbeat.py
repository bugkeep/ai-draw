import asyncio
import time
from .models import Session
from .session import SessionManager
from events import EventBus, EventType, ClientErrorEvent


class HeartbeatManager:
    def __init__(
        self,
        session_manager: SessionManager,
        event_bus: EventBus | None = None,
        interval: float = 30,
        timeout: float = 60,
    ):
        self.session_manager = session_manager
        self.event_bus = event_bus or EventBus()
        self.interval = interval
        self.timeout = timeout
        self._last_heartbeat: dict[str, float] = {}
        self._running = False

    async def record(self, client_id: str):
        self._last_heartbeat[client_id] = time.time()

    def is_alive(self, client_id: str) -> bool:
        last = self._last_heartbeat.get(client_id)
        if last is None:
            return True
        return (time.time() - last) < self.timeout

    async def check_dead(self) -> list[str]:
        dead = []
        now = time.time()
        for client_id, last in list(self._last_heartbeat.items()):
            if (now - last) > self.timeout:
                dead.append(client_id)
                await self.event_bus.dispatch(
                    ClientErrorEvent(
                        client_addr=client_id,
                        client_id=client_id,
                        error="heartbeat timeout",
                    )
                )
                session = await self.session_manager.get_by_client(client_id)
                if session:
                    await self.session_manager.destroy(session.session_id)
                del self._last_heartbeat[client_id]
        return dead

    async def start(self):
        self._running = True
        while self._running:
            await self.check_dead()
            await asyncio.sleep(self.interval)

    def stop(self):
        self._running = False

    @property
    def active_count(self) -> int:
        now = time.time()
        return sum(
            1 for last in self._last_heartbeat.values()
            if (now - last) < self.timeout
        )

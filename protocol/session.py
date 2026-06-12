import time
from .models import Session
from events import EventBus, EventType, ClientConnectEvent, ClientDisconnectEvent


class SessionManager:
    def __init__(self, event_bus: EventBus | None = None, timeout: float = 300):
        self.event_bus = event_bus or EventBus()
        self.timeout = timeout
        self._sessions: dict[str, Session] = {}
        self._client_sessions: dict[str, str] = {}

    async def create(self, client_id: str, metadata: dict | None = None) -> Session:
        session = Session(client_id=client_id, metadata=metadata or {})
        self._sessions[session.session_id] = session
        self._client_sessions[client_id] = session.session_id

        await self.event_bus.dispatch(
            ClientConnectEvent(
                client_addr=client_id,
                client_id=session.session_id,
            )
        )

        return session

    async def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session and not session.is_expired(self.timeout):
            session.touch()
            return session
        if session:
            await self.destroy(session_id)
        return None

    async def get_by_client(self, client_id: str) -> Session | None:
        session_id = self._client_sessions.get(client_id)
        if session_id:
            return await self.get(session_id)
        return None

    async def destroy(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session:
            self._client_sessions.pop(session.client_id, None)
            await self.event_bus.dispatch(
                ClientDisconnectEvent(
                    client_addr=session.client_id,
                    client_id=session_id,
                    reason="session destroyed",
                )
            )
            return True
        return False

    async def cleanup_expired(self) -> int:
        expired = [
            sid
            for sid, session in self._sessions.items()
            if session.is_expired(self.timeout)
        ]
        for sid in expired:
            await self.destroy(sid)
        return len(expired)

    def get_all(self) -> list[Session]:
        return [
            s for s in self._sessions.values()
            if not s.is_expired(self.timeout)
        ]

    @property
    def active_count(self) -> int:
        return len([
            s for s in self._sessions.values()
            if not s.is_expired(self.timeout)
        ])

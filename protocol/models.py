from dataclasses import dataclass, field
from typing import Any
import uuid
import time


@dataclass
class ProtocolMessage:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    action: str = ""
    payload: dict = field(default_factory=dict)
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "payload": self.payload,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProtocolMessage":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            action=data.get("action", ""),
            payload=data.get("payload", {}),
            session_id=data.get("session_id", ""),
            timestamp=data.get("timestamp", time.time()),
        )


@dataclass
class ProtocolResponse:
    id: str = ""
    ok: bool = True
    data: Any = None
    error_msg: str = ""
    session_id: str = ""

    def to_dict(self) -> dict:
        result = {"id": self.id, "ok": self.ok}
        if self.ok:
            result["data"] = self.data
        else:
            result["error"] = self.error_msg
        if self.session_id:
            result["session_id"] = self.session_id
        return result

    @classmethod
    def success(cls, request_id: str, data: Any = None, session_id: str = "") -> "ProtocolResponse":
        return cls(id=request_id, ok=True, data=data, session_id=session_id)

    @classmethod
    def fail(cls, request_id: str, error: str, session_id: str = "") -> "ProtocolResponse":
        return cls(id=request_id, ok=False, error_msg=error, session_id=session_id)


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    client_id: str = ""
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def touch(self):
        self.last_active = time.time()

    def is_expired(self, timeout: float = 300) -> bool:
        return (time.time() - self.last_active) > timeout


@dataclass
class QueuedMessage:
    message: ProtocolMessage
    client_id: str = ""
    retries: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)

    def can_retry(self) -> bool:
        return self.retries < self.max_retries


@dataclass
class EventPushEnvelope:
    """Wire format for event pushes — used identically for replay and live."""

    topic: str
    event_type: str
    data: dict
    timestamp: float
    run_id: str = ""

    def to_dict(self) -> dict:
        return {
            "type": "event_push",
            "topic": self.topic,
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
        }


import fnmatch
import asyncio
from dataclasses import dataclass, field
from fastapi import WebSocket
from .base import BaseEvent


@dataclass
class Subscription:
    sub_id: str
    writer: asyncio.StreamWriter | None = None
    ws: WebSocket | None = None
    topics: list[str] = field(default_factory=lambda: ["*"])
    scope: str = "global"

    def matches(self, event: BaseEvent) -> bool:
        topic = event.get_topic()
        if not any(fnmatch.fnmatch(topic, pat) for pat in self.topics):
            return False

        if self.scope == "global":
            return True

        if self.scope.startswith("run:"):
            run_id = event.run_id or event.data.get("run_id", "")
            return run_id and self.scope[4:] == run_id

        return False

    async def send(self, message: str):
        if self.ws:
            await self.ws.send_text(message)
        elif self.writer:
            self.writer.write((message + "\n").encode())
            await self.writer.drain()

import asyncio
import json


class DaemonClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self):
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port
        )

    async def send(self, action: str, payload: dict) -> dict:
        if not self._writer:
            await self.connect()
        message = json.dumps({"action": action, "payload": payload}) + "\n"
        self._writer.write(message.encode())
        await self._writer.drain()
        data = await self._reader.readline()
        if not data:
            raise ConnectionError("Daemon closed connection")
        return json.loads(data.decode().strip())

    async def close(self):
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None

import asyncio
import json


class DaemonClient:
    """TCP client to the local daemon process.

    Each ``send()`` opens a **new** connection so concurrent HTTP requests
    never share a ``StreamReader`` / ``StreamWriter`` pair (which would
    raise ``readuntil() called while another coroutine is already
    waiting``).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port

    async def send(self, action: str, payload: dict) -> dict:
        reader, writer = await asyncio.open_connection(
            self.host, self.port
        )
        try:
            message = json.dumps({"action": action, "payload": payload}) + "\n"
            writer.write(message.encode())
            await writer.drain()
            data = await reader.readline()
            if not data:
                raise ConnectionError("Daemon closed connection")
            return json.loads(data.decode().strip())
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

import asyncio
import json
import inspect
import uuid
from typing import Callable
from events import (
    EventBus,
    BaseEvent,
    EventType,
    SocketStartEvent,
    SocketStopEvent,
    SocketErrorEvent,
    ClientConnectEvent,
    ClientDisconnectEvent,
    ClientErrorEvent,
)


class TCPServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.event_bus = EventBus()
        self._handlers: dict[str, Callable] = {}

    def register_handler(self, action: str, handler: Callable):
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"Handler for '{action}' must be async")
        self._handlers[action] = handler

    async def start(self):
        await self.event_bus.dispatch(
            SocketStartEvent(host=self.host, port=self.port)
        )

        try:
            server = await asyncio.start_server(
                self._handle_client, self.host, self.port
            )
            addr = server.sockets[0].getsockname()
            print(f"TCP Server listening on {addr[0]}:{addr[1]}")

            async with server:
                await server.serves_forever()
        except Exception as e:
            await self.event_bus.dispatch(
                SocketErrorEvent(error=str(e), host=self.host, port=self.port)
            )
            raise
        finally:
            await self.event_bus.dispatch(SocketStopEvent(reason="server shutdown"))

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        addr = writer.get_extra_info("peername")
        client_id = str(uuid.uuid4())[:8]
        client_addr = f"{addr[0]}:{addr[1]}"

        await self.event_bus.dispatch(
            ClientConnectEvent(client_addr=client_addr, client_id=client_id)
        )

        try:
            while True:
                data = await reader.readline()
                if not data:
                    break

                try:
                    message = json.loads(data.decode().strip())
                except json.JSONDecodeError as e:
                    error_response = json.dumps({"error": f"Invalid JSON: {e}"}) + "\n"
                    writer.write(error_response.encode())
                    await writer.drain()
                    continue

                action = message.get("action")
                payload = message.get("payload", {})

                await self.event_bus.dispatch(
                    BaseEvent(EventType.VOICE_RECEIVED, payload)
                )

                if action in self._handlers:
                    result = await self._handlers[action](payload)
                else:
                    result = {"error": f"Unknown action: {action}"}

                response = json.dumps(result) + "\n"
                writer.write(response.encode())
                await writer.drain()

        except asyncio.IncompleteReadError:
            await self.event_bus.dispatch(
                ClientDisconnectEvent(
                    client_addr=client_addr,
                    client_id=client_id,
                    reason="connection closed",
                )
            )
        except Exception as e:
            await self.event_bus.dispatch(
                ClientErrorEvent(
                    client_addr=client_addr, client_id=client_id, error=str(e)
                )
            )
        finally:
            await self.event_bus.dispatch(
                ClientDisconnectEvent(
                    client_addr=client_addr, client_id=client_id, reason="cleanup"
                )
            )
            writer.close()
            await writer.wait_closed()


async def handle_chat(payload: dict) -> dict:
    return {
        "code": "",
        "description": f"Received: {payload.get('message', '')}",
        "tool_calls": 0
    }


async def main():
    server = TCPServer()
    server.register_handler("chat", handle_chat)
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())

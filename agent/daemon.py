import asyncio
import json
from typing import Callable, Any
from events import EventBus, BaseEvent, EventType


class TCPServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.event_bus = EventBus()
        self._handlers: dict[str, Callable] = {}
    
    def register_handler(self, action: str, handler: Callable):
        self._handlers[action] = handler
    
    async def start(self):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        print(f"TCP Server listening on {addr[0]}:{addr[1]}")
        
        async with server:
            await server.serve_forever()
    
    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        print(f"Client connected: {addr}")
        
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                
                message = json.loads(data.decode().strip())
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
            pass
        finally:
            print(f"Client disconnected: {addr}")
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

import asyncio
import json
import inspect
import uuid
import os
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
    EventBroadcaster,
    Subscription,
)
from protocol import JsonRpcRequest
from core.app import _replay_events, events_file
from traces import DaemonTracer
from providers.openai_provider import OpenAIProvider
from providers.bailian_provider import BailianProvider
from tools import ALL_TOOLS
from tools.registry import ToolRegistry
from agent.runner import AgentRunner, AgentConfig


class TCPServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765, broadcaster: EventBroadcaster | None = None, event_bus: EventBus | None = None, tracer: DaemonTracer | None = None):
        self.host = host
        self.port = port
        self.broadcaster = broadcaster
        self.event_bus = event_bus or EventBus()
        self.tracer = tracer
        self._handlers: dict[str, Callable] = {}
        self._runner: AgentRunner | None = None
        self._current_provider = ""
        self._current_api_key = ""

    def register_handler(self, action: str, handler: Callable):
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"Handler for '{action}' must be async")
        self._handlers[action] = handler

    def init_runner(self, provider: str = "openai", api_key: str = ""):
        providers = {
            "openai": lambda key: OpenAIProvider(
                api_key=key or os.environ.get("OPENAI_API_KEY", ""),
                model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            ),
            "bailian": lambda key: BailianProvider(
                api_key=key or os.environ.get("DASHSCOPE_API_KEY", ""),
                model=os.environ.get("BAILIAN_MODEL", "qwen-plus"),
            ),
        }
        provider_fn = providers.get(provider, providers["openai"])
        prov = provider_fn(api_key)
        registry = ToolRegistry()
        for tool_cls in ALL_TOOLS:
            registry.register(tool_cls())
        config = AgentConfig(provider=prov, registry=registry, event_bus=self.event_bus, tracer=self.tracer)
        self._runner = AgentRunner(config)
        self._current_provider = provider
        self._current_api_key = api_key

    async def handle_chat(self, payload: dict) -> dict:
        message = payload.get("message", "")
        canvas_state = payload.get("canvas_state", {})
        provider = payload.get("provider", "openai")
        api_key = payload.get("api_key", "")

        if not self._runner or provider != self._current_provider or api_key != self._current_api_key:
            self.init_runner(provider, api_key)

        result = await self._runner.run(message=message, canvas_state=canvas_state)
        return {
            "run_id": result.run_id,
            "content": result.content,
            "code": result.code,
            "description": result.description,
            "tool_calls": len(result.tool_calls),
            "success": result.success,
            "error": result.error,
            "rounds": result.rounds,
        }

    async def start(self):
        self.register_handler("chat", self.handle_chat)
        await self.event_bus.dispatch(
            SocketStartEvent(host=self.host, port=self.port)
        )

        try:
            self._server = await asyncio.start_server(
                self._handle_client, self.host, self.port
            )
            addr = self._server.sockets[0].getsockname()
            self.port = addr[1]
            print(f"TCP Server listening on {addr[0]}:{addr[1]}")

            async with self._server:
                await self._server.serve_forever()
        except Exception as e:
            await self.event_bus.dispatch(
                SocketErrorEvent(error=str(e), host=self.host, port=self.port)
            )
            raise
        finally:
            await self.event_bus.dispatch(SocketStopEvent(reason="server shutdown"))

    def stop(self):
        if hasattr(self, "_server") and self._server:
            self._server.close()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        addr = writer.get_extra_info("peername")
        client_id = str(uuid.uuid4())[:8]
        client_addr = f"{addr[0]}:{addr[1]}"

        await self.event_bus.dispatch(
            ClientConnectEvent(client_addr=client_addr, client_id=client_id)
        )

        if self.broadcaster:
            self.broadcaster.subscribe(Subscription(
                sub_id=f"tcp-{client_id}",
                writer=writer,
                topics=["*"],
                scope="global",
            ))

        try:
            while True:
                data = await reader.readline()
                if not data:
                    break

                try:
                    raw = json.loads(data.decode().strip())
                    req = JsonRpcRequest.model_validate(raw)
                except Exception:
                    err = json.dumps({"error": "parse error"}) + "\n"
                    writer.write(err.encode())
                    await writer.drain()
                    continue

                action = req.action
                payload = req.payload

                if self.tracer:
                    self.tracer.on_ipc_request(action, payload, client_id=client_id)

                await self.event_bus.dispatch(
                    BaseEvent(EventType.VOICE_RECEIVED, payload)
                )

                if action == "event_subscribe":
                    topics = payload.get("topics", ["*"])
                    scope = payload.get("scope", "global")

                    count = 0
                    run_id = ""
                    if scope.startswith("run:"):
                        run_id = scope[4:]
                        fp = events_file(run_id)
                        if os.path.isfile(fp):
                            with open(fp, encoding="utf-8") as f:
                                count = sum(1 for line in f if line.strip())

                    result = {"replayed_count": count}
                    response = json.dumps(result) + "\n"
                    writer.write(response.encode())
                    await writer.drain()

                    if self.tracer:
                        self.tracer.on_ipc_response("event_subscribe", result, run_id=run_id, client_id=client_id)

                    if run_id:
                        await _replay_events(run_id, writer)

                    if self.broadcaster:
                        self.broadcaster.subscribe(Subscription(
                            sub_id=f"sub-{client_id}",
                            writer=writer,
                            topics=topics,
                            scope=scope,
                        ))

                    continue
                elif action in self._handlers:
                    result = await self._handlers[action](payload)
                else:
                    result = {"error": f"Unknown action: {action}"}

                response = json.dumps(result) + "\n"
                writer.write(response.encode())
                await writer.drain()

                if self.tracer:
                    self.tracer.on_ipc_response(action, result, run_id=result.get("run_id", ""), client_id=client_id)

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
            if self.broadcaster:
                self.broadcaster.unsubscribe(writer)
            await self.event_bus.dispatch(
                ClientDisconnectEvent(
                    client_addr=client_addr, client_id=client_id, reason="cleanup"
                )
            )
            writer.close()
            await writer.wait_closed()


async def main():
    server = TCPServer()
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())

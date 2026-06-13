import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from events import EventBus, EventBroadcaster
from agent.daemon import TCPServer
from core.app import JsonlRecorder, TraceHandler, AgentRunHandler
from traces import DaemonTracer
from .routes import router
from .daemon_client import DaemonClient

FRONTEND_DIR = Path(__file__).parent.parent / "frontend-dist"
DAEMON_PORT = 8765


def create_app() -> FastAPI:
    event_bus = EventBus()
    tracer = DaemonTracer()
    _trace_handler = TraceHandler(event_bus, tracer)
    broadcaster = EventBroadcaster(event_bus, tracer=tracer)
    _recorder = JsonlRecorder(event_bus)
    daemon = TCPServer(port=DAEMON_PORT, broadcaster=broadcaster, event_bus=event_bus, tracer=tracer)
    agent_handler = AgentRunHandler(daemon)
    daemon.register_handler("run", agent_handler.handle_run)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tracer.start()
        asyncio.create_task(daemon.start())
        yield
        await agent_handler.cancel_all_runs()
        await tracer.stop()
        daemon.stop()

    app = FastAPI(title="AI Voice Draw", version="0.1.0", lifespan=lifespan)
    app.state.event_bus = event_bus
    app.state.broadcaster = broadcaster
    app.state.daemon = daemon
    app.state.daemon_client = DaemonClient(port=DAEMON_PORT)

    app.include_router(router, prefix="/api")

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        client_id = str(uuid.uuid4())[:8]
        await broadcaster.connect(ws, client_id)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.disconnect(client_id)

    if FRONTEND_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            file_path = FRONTEND_DIR / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(FRONTEND_DIR / "index.html"))
    else:

        @app.get("/")
        async def index():
            return {"error": "Frontend not built. Run: cd frontend-app && npm run build"}

    return app

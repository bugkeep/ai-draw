import uuid
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from events import EventBus, EventBroadcaster
from .routes import router

FRONTEND_DIR = Path(__file__).parent.parent / "frontend-dist"


def create_app() -> FastAPI:
    app = FastAPI(title="AI Voice Draw", version="0.1.0")

    event_bus = EventBus()
    broadcaster = EventBroadcaster(event_bus)
    app.state.event_bus = event_bus
    app.state.broadcaster = broadcaster

    app.include_router(router, prefix="/api")

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        client_id = str(uuid.uuid4())[:8]
        await broadcaster.connect(ws, client_id)
        try:
            while True:
                data = await ws.receive_text()
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

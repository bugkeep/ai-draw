from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from studio.api import create_runtime, create_store, router as studio_router


ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend-dist"


def create_app() -> FastAPI:
    load_dotenv(ROOT / ".env", override=False)
    store = create_store()
    runtime = create_runtime(store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await runtime.close()
        store.close()

    app = FastAPI(title="Draw Agent Studio", version="0.1.0", lifespan=lifespan)
    app.state.studio_store = store
    app.state.drawing_runtime = runtime
    app.include_router(studio_router)

    if FRONTEND_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return FileResponse(FRONTEND_DIR / "index.html")
    else:
        @app.get("/")
        async def missing_frontend():
            return JSONResponse({"error": "Build the frontend with npm run build"}, status_code=503)

    return app

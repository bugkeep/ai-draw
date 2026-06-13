from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .routes import router

FRONTEND_DIR = Path(__file__).parent.parent / "frontend-dist"


def create_app() -> FastAPI:
    app = FastAPI(title="AI Voice Draw", version="0.1.0")

    app.include_router(router, prefix="/api")

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

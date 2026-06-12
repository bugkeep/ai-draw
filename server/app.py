from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="AI Voice Draw", version="0.1.0")
    
    app.include_router(router, prefix="/api")
    
    app.mount("/static", StaticFiles(directory="frontend"), name="static")
    
    @app.get("/")
    async def index():
        return FileResponse("frontend/index.html")
    
    return app

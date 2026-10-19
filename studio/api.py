import asyncio
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from .runtime import DrawingRuntime
from .llm import ModelGatewayError, test_connection as probe_model_connection
from .settings import ModelSettings, SettingsUpdate
from .store import StudioStore, VersionConflict


router = APIRouter(prefix="/api/studio", tags=["studio"])


class NewSession(BaseModel):
    title: str = Field(default="未命名画布", max_length=120)


class RenameSession(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=120)


class SendMessage(BaseModel):
    message: str = Field(min_length=1, max_length=3000)


class SceneSnapshot(BaseModel):
    version: int = Field(ge=0)
    elements: list[dict[str, Any]]
    selection: list[str] = Field(default_factory=list)
    client_id: str = Field(default="", max_length=64)


class HistoryCommand(BaseModel):
    direction: Literal["undo", "redo"]


def studio_store(request: Request) -> StudioStore:
    return request.app.state.studio_store


@router.get("/health")
async def health(request: Request):
    config = request.app.state.model_settings.load()
    return {"status": "ok", "api_revision": 4, "provider_configured": config.configured,
            "model": config.model, "provider": config.provider}


def settings_view(request: Request) -> dict[str, Any]:
    return {**request.app.state.model_settings.view(), "busy": request.app.state.drawing_runtime.busy}


@router.get("/settings")
async def get_settings(request: Request):
    return settings_view(request)


@router.put("/settings")
async def save_settings(request: Request, body: SettingsUpdate):
    if request.app.state.drawing_runtime.busy:
        raise HTTPException(409, "绘图任务正在排队或运行，请等待全部任务结束后保存配置。")
    try:
        request.app.state.model_settings.save(body)
        return settings_view(request)
    except VersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/settings/test")
async def test_settings(request: Request, body: SettingsUpdate):
    try:
        config = request.app.state.model_settings.candidate(body)
        return await probe_model_connection(config)
    except ModelGatewayError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/sessions")
async def list_sessions(request: Request):
    return studio_store(request).list_sessions()


@router.post("/sessions")
async def create_session(request: Request, body: NewSession):
    return studio_store(request).create_session(body.title)


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str):
    try:
        return studio_store(request).get_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/sessions/{session_id}/messages")
async def send_message(request: Request, session_id: str, body: SendMessage):
    try:
        return request.app.state.drawing_runtime.submit(session_id, body.message.strip())
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/sessions/{session_id}/agents")
async def list_session_agents(request: Request, session_id: str):
    try:
        store = studio_store(request)
        store.get_session(session_id)
        return store.agents.list_agents(session_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/sessions/{session_id}/agents/{agent_id}")
async def get_session_agent(request: Request, session_id: str, agent_id: str):
    try:
        return studio_store(request).agents.get_agent(session_id, agent_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/sessions/{session_id}")
async def rename_session(request: Request, session_id: str, body: RenameSession):
    try:
        return studio_store(request).rename_session(session_id, body.title)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/documents/{document_id}/scene")
async def save_scene(request: Request, document_id: str, body: SceneSnapshot):
    try:
        return studio_store(request).save_snapshot(document_id, body.version, body.elements, body.selection, body.client_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except VersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str):
    runtime: DrawingRuntime = request.app.state.drawing_runtime
    if runtime.session_busy(session_id):
        raise HTTPException(409, "此画布有正在运行或排队的绘图任务，请停止任务后再删除。")
    try:
        return studio_store(request).delete_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/documents/{document_id}/history")
async def history(request: Request, document_id: str, body: HistoryCommand):
    try:
        return studio_store(request).history_step(document_id, body.direction)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/tasks/{task_id}")
async def get_task(request: Request, task_id: str):
    try:
        return studio_store(request).get_task(task_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/tasks/{task_id}/logs")
async def task_logs(request: Request, task_id: str):
    try:
        studio_store(request).get_task(task_id)
        return studio_store(request).tool_logs(task_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str):
    try:
        request.app.state.drawing_runtime.cancel(task_id)
        return {"task_id": task_id, "status": "cancelling"}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.websocket("/documents/{document_id}/events")
async def document_events(websocket: WebSocket, document_id: str, after: int = 0):
    store: StudioStore = websocket.app.state.studio_store
    try:
        store.get_document(document_id)
    except KeyError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()
    store.subscribe(document_id, queue)
    seen = after
    try:
        while True:
            batch = store.events_after(document_id, seen)
            for event in batch:
                await websocket.send_json(event)
                seen = event["seq"]
            if len(batch) < 500:
                break
        while True:
            event_task = asyncio.create_task(queue.get())
            receive_task = asyncio.create_task(websocket.receive_text())
            done, pending = await asyncio.wait({event_task, receive_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if receive_task in done:
                await receive_task
                continue
            event = event_task.result()
            if event["seq"] > seen:
                await websocket.send_json(event)
                seen = event["seq"]
                if event["type"] == "session_deleted":
                    await websocket.close(code=1000)
                    return
    except WebSocketDisconnect:
        pass
    finally:
        store.unsubscribe(document_id, queue)


def create_store() -> StudioStore:
    path = Path(os.getenv("DRAW_AGENT_DB", "runs/studio.sqlite3"))
    return StudioStore(path)


def create_runtime(store: StudioStore, settings: ModelSettings) -> DrawingRuntime:
    return DrawingRuntime(store, settings)

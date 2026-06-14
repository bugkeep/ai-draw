from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional
import os

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    canvas_state: Optional[dict] = None
    history: Optional[list[dict]] = None
    provider: Optional[str] = None
    api_key: Optional[str] = ""


class ChatResponse(BaseModel):
    run_id: str = ""
    content: str = ""
    code: str = ""
    description: str = ""
    tool_calls: int = 0
    success: bool = True
    error: str = ""
    rounds: int = 0
    latency_ms: float = 0


class SessionCreateRequest(BaseModel):
    provider: Optional[str] = "bailian"
    api_key: Optional[str] = ""
    mode: Optional[str] = "agent"
    title: Optional[str] = ""


class SessionCreateResponse(BaseModel):
    session_id: str = ""
    error: str = ""


class SessionMessageRequest(BaseModel):
    session_id: str
    message: str
    canvas_state: Optional[dict] = None


class SessionMessageResponse(BaseModel):
    run_id: str = ""
    error: str = ""


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    client = request.app.state.daemon_client
    try:
        result = await client.send("chat", {
            "message": body.message,
            "canvas_state": body.canvas_state or {},
            "history": body.history or [],
            "provider": body.provider or os.environ.get("LLM_PROVIDER", "openai"),
            "api_key": body.api_key or "",
        })
        return ChatResponse(**result)
    except Exception as e:
        return ChatResponse(success=False, error=str(e))


@router.post("/session.create", response_model=SessionCreateResponse)
async def session_create(request: Request, body: SessionCreateRequest):
    client = request.app.state.daemon_client
    try:
        result = await client.send("session.create", body.model_dump())
        return SessionCreateResponse(**result)
    except Exception as e:
        return SessionCreateResponse(error=str(e))


@router.post("/session.send_message", response_model=SessionMessageResponse)
async def session_send_message(request: Request, body: SessionMessageRequest):
    client = request.app.state.daemon_client
    try:
        result = await client.send("session.send_message", body.model_dump())
        return SessionMessageResponse(**result)
    except Exception as e:
        return SessionMessageResponse(error=str(e))

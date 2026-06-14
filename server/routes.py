import asyncio
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    canvas_state: Optional[dict] = None
    provider: Optional[str] = "bailian"
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


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    client = request.app.state.daemon_client
    try:
        result = await client.send("chat", {
            "message": body.message,
            "canvas_state": body.canvas_state or {},
            "provider": body.provider or "openai",
            "api_key": body.api_key or "",
        })
        return ChatResponse(**result)
    except Exception as e:
        return ChatResponse(success=False, error=str(e))

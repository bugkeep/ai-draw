from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    canvas_state: Optional[dict] = None


class ChatResponse(BaseModel):
    code: str
    description: str
    tool_calls: int


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # Placeholder - will connect to TCP agent later
    return ChatResponse(
        code="",
        description=f"Received: {request.message}",
        tool_calls=0
    )

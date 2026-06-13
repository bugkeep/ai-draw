import os
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from providers.openai_provider import OpenAIProvider
from tools import ALL_TOOLS
from tools.registry import ToolRegistry
from agent.runner import AgentRunner, AgentConfig

router = APIRouter()

_provider = OpenAIProvider(
    api_key=os.environ.get("OPENAI_API_KEY", ""),
    model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
)
_registry = ToolRegistry()
for tool_cls in ALL_TOOLS:
    _registry.register(tool_cls())

_runner = AgentRunner(AgentConfig(provider=_provider, registry=_registry))


class ChatRequest(BaseModel):
    message: str
    canvas_state: Optional[dict] = None


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
async def chat(request: ChatRequest):
    result = await _runner.run(
        message=request.message,
        canvas_state=request.canvas_state,
    )
    return ChatResponse(
        run_id=result.run_id,
        content=result.content,
        code=result.code,
        description=result.description,
        tool_calls=len(result.tool_calls),
        success=result.success,
        error=result.error,
        rounds=result.rounds,
    )

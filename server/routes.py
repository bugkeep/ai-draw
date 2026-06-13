import os
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from providers.openai_provider import OpenAIProvider
from providers.bailian_provider import BailianProvider
from tools import ALL_TOOLS
from tools.registry import ToolRegistry
from agent.runner import AgentRunner, AgentConfig

router = APIRouter()

_providers = {
    "openai": lambda api_key: OpenAIProvider(
        api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
    ),
    "bailian": lambda api_key: BailianProvider(
        api_key=api_key or os.environ.get("DASHSCOPE_API_KEY", ""),
        model=os.environ.get("BAILIAN_MODEL", "qwen-plus"),
    ),
}

_registry = ToolRegistry()
for tool_cls in ALL_TOOLS:
    _registry.register(tool_cls())


def _get_runner(provider: str = "openai", api_key: str = "") -> AgentRunner:
    provider_fn = _providers.get(provider, _providers["openai"])
    prov = provider_fn(api_key)
    return AgentRunner(AgentConfig(provider=prov, registry=_registry))


class ChatRequest(BaseModel):
    message: str
    canvas_state: Optional[dict] = None
    provider: Optional[str] = "openai"
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
async def chat(request: ChatRequest):
    runner = _get_runner(
        provider=request.provider or "openai",
        api_key=request.api_key or "",
    )
    result = await runner.run(
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

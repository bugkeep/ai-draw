import os
from fastapi import APIRouter, Request
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


def _get_runner(provider: str = "openai", api_key: str = "", event_bus=None) -> AgentRunner:
    provider_fn = _providers.get(provider, _providers["openai"])
    prov = provider_fn(api_key)
    config = AgentConfig(provider=prov, registry=_registry)
    if event_bus:
        config.event_bus = event_bus
    return AgentRunner(config)


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
async def chat(request: Request, body: ChatRequest):
    event_bus = request.app.state.event_bus
    runner = _get_runner(
        provider=body.provider or "openai",
        api_key=body.api_key or "",
        event_bus=event_bus,
    )
    result = await runner.run(
        message=body.message,
        canvas_state=body.canvas_state,
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

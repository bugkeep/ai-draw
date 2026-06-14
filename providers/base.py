from dataclasses import dataclass, field
from typing import Any, Protocol
import json


@dataclass
class ToolCall:
    id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }

    @classmethod
    def from_openai(cls, data: dict) -> "ToolCall":
        func = data.get("function", {})
        args = func.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return cls(
            id=data.get("id", ""),
            name=func.get("name", ""),
            arguments=args,
        )

    @classmethod
    def from_anthropic(cls, data: dict) -> "ToolCall":
        args = data.get("input", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            arguments=args,
        )


@dataclass
class LLMResponse:
    content: str = ""
    model: str = ""
    tokens_used: int = 0
    context_window: int = 0     # model's max context window in tokens
    context_pct: float = 0.0    # tokens_used / context_window
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "model": self.model,
            "tokens_used": self.tokens_used,
            "context_window": self.context_window,
            "context_pct": self.context_pct,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
        }


# ── context window lookup ────────────────────────────────────────────

_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128000,
    "gpt-4o-2024-": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-3.5-turbo": 16385,
    "o1": 200000,
    "o1-mini": 128000,
    "o3-mini": 200000,
    "qwen-plus": 131072,
    "qwen-max": 32768,
    "qwen-turbo": 131072,
    "deepseek": 65536,
    "claude": 200000,
}


def get_context_window(model: str) -> int:
    """Return the approximate context window (in tokens) for a model name.
    Falls back to 128000 for unknown models.
    """
    for prefix, size in _MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return size
    return 128000


class LLMProvider(Protocol):
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        ...

    async def achat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        *,
        step: int = 0,
    ) -> LLMResponse:
        ...

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
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "model": self.model,
            "tokens_used": self.tokens_used,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
        }


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

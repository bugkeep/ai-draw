import json
from dataclasses import dataclass, field
from typing import Any
from providers.base import LLMProvider, LLMResponse, ToolCall
from tools.base import ToolResult
from tools.registry import ToolRegistry
from events import EventBus, EventType, BaseEvent
from .prompts import SYSTEM_PROMPT


@dataclass
class AgentResponse:
    content: str = ""
    code: str = ""
    description: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    success: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        result = {
            "content": self.content,
            "code": self.code,
            "description": self.description,
            "tool_calls": self.tool_calls,
        }
        if not self.success:
            result["error"] = self.error
        return result


class DrawingAgent:
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        event_bus: EventBus | None = None,
        max_tool_rounds: int = 5,
    ):
        self.provider = provider
        self.registry = registry
        self.event_bus = event_bus or EventBus()
        self.max_tool_rounds = max_tool_rounds

    async def chat(self, message: str, canvas_state: dict | None = None) -> AgentResponse:
        canvas_state = canvas_state or {}
        canvas_desc = self._format_canvas_state(canvas_state)

        system_prompt = SYSTEM_PROMPT.format(canvas_state=canvas_desc)
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]

        tools = self.registry.get_tool_definitions()

        all_code: list[str] = []
        all_desc: list[str] = []
        all_tool_calls: list[dict] = []
        last_content = ""

        for round_num in range(self.max_tool_rounds):
            try:
                response = await self.provider.achat(
                    messages=messages,
                    tools=tools if tools else None,
                )
            except Exception as e:
                await self._dispatch_error("llm_error", str(e))
                return AgentResponse(
                    success=False,
                    error=f"LLM call failed: {e}",
                    content=last_content,
                    code="\n".join(all_code),
                    description="\n".join(all_desc),
                )

            last_content = response.content or last_content

            if not response.tool_calls:
                break

            tool_results = []
            for tc in response.tool_calls:
                await self._dispatch_tool_call(tc)

                result = self.registry.execute(tc.name, **tc.arguments)

                await self._dispatch_tool_result(tc, result)

                tool_results.append({
                    "tool_call_id": tc.id,
                    "role": "tool",
                    "content": json.dumps(result.to_dict(), ensure_ascii=False),
                })

                if not result.is_error:
                    if result.code:
                        all_code.append(result.code)
                    if result.description:
                        all_desc.append(result.description)
                all_tool_calls.append({
                    "name": tc.name,
                    "arguments": tc.arguments,
                    "is_error": result.is_error,
                })

            messages.append({
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [tc.to_dict() for tc in response.tool_calls],
            })
            messages.extend(tool_results)

        return AgentResponse(
            content=last_content,
            code="\n".join(all_code),
            description="\n".join(all_desc),
            tool_calls=all_tool_calls,
        )

    def _format_canvas_state(self, state: dict) -> str:
        if not state:
            return "Empty canvas"
        objects = state.get("objects", [])
        if not objects:
            return "Empty canvas"
        parts = []
        for i, obj in enumerate(objects):
            obj_type = obj.get("type", "unknown")
            left = obj.get("left", 0)
            top = obj.get("top", 0)
            fill = obj.get("fill", "")
            parts.append(f"  [{i}] {obj_type} at ({left},{top}) fill={fill}")
        return f"{len(objects)} objects:\n" + "\n".join(parts)

    async def _dispatch_tool_call(self, tc: ToolCall):
        await self.event_bus.dispatch(
            BaseEvent(EventType.TOOL_CALL, {"name": tc.name, "arguments": tc.arguments})
        )

    async def _dispatch_tool_result(self, tc: ToolCall, result: ToolResult):
        event_type = EventType.TOOL_RESULT if not result.is_error else EventType.TOOL_ERROR
        await self.event_bus.dispatch(
            BaseEvent(event_type, {"name": tc.name, "is_error": result.is_error, "error": result.error})
        )

    async def _dispatch_error(self, error_type: str, message: str):
        await self.event_bus.dispatch(
            BaseEvent(EventType.ERROR, {"type": error_type, "message": message})
        )

import json
import time
import random
import string
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from providers.base import LLMProvider, LLMResponse, ToolCall
from tools.base import ToolResult
from tools.registry import ToolRegistry
from events import EventBus, EventType, BaseEvent
from .prompts import SYSTEM_PROMPT


def new_run_id() -> str:
    t = time.localtime()
    ts = time.strftime("%y%m%d-%H%M%S", t)
    rand = "".join(random.choices(string.digits, k=6))
    return f"{ts}-{rand}"


class ToolResultStatus(Enum):
    SUCCESS = "success"
    FAILED_EXECUTION = "failed_execution"
    FAILED_NOT_FOUND = "failed_not_found"
    FAILED_INVALID_ARGS = "failed_invalid_args"
    FAILED_UNKNOWN = "failed_unknown"


def classify_tool_error(result: ToolResult, tool_name: str, registry: ToolRegistry) -> ToolResultStatus:
    if not result.is_error:
        return ToolResultStatus.SUCCESS
    error = result.error.lower()
    if "unknown tool" in error:
        return ToolResultStatus.FAILED_NOT_FOUND
    if "required" in error or "missing" in error or "invalid" in error:
        return ToolResultStatus.FAILED_INVALID_ARGS
    if "failed" in error or "error" in error or "exception" in error:
        return ToolResultStatus.FAILED_EXECUTION
    return ToolResultStatus.FAILED_UNKNOWN


@dataclass
class AgentResponse:
    run_id: str = ""
    content: str = ""
    code: str = ""
    description: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    success: bool = True
    error: str = ""
    rounds: int = 0

    def to_dict(self) -> dict:
        result = {
            "run_id": self.run_id,
            "content": self.content,
            "code": self.code,
            "description": self.description,
            "tool_calls": self.tool_calls,
            "rounds": self.rounds,
        }
        if not self.success:
            result["error"] = self.error
        return result


@dataclass
class AgentConfig:
    provider: LLMProvider | None = None
    registry: ToolRegistry | None = None
    event_bus: EventBus | None = None
    system_prompt: str = SYSTEM_PROMPT
    max_rounds: int = 5


class AgentRunner:
    def __init__(self, config: AgentConfig | None = None):
        config = config or AgentConfig()
        self.provider = config.provider
        self.registry = config.registry or ToolRegistry()
        self.event_bus = config.event_bus or EventBus()
        self.system_prompt = config.system_prompt
        self.max_rounds = config.max_rounds

        if not self.provider:
            raise ValueError("LLMProvider is required")

    def assemble(
        self,
        provider: LLMProvider | None = None,
        registry: ToolRegistry | None = None,
        event_bus: EventBus | None = None,
        tools: list | None = None,
    ):
        if provider:
            self.provider = provider
        if registry:
            self.registry = registry
        if event_bus:
            self.event_bus = event_bus
        if tools:
            for tool in tools:
                self.registry.register(tool)
        return self

    async def run(self, message: str, canvas_state: dict | None = None) -> AgentResponse:
        run_id = new_run_id()
        canvas_state = canvas_state or {}
        canvas_desc = self._format_canvas_state(canvas_state)
        system_prompt = self.system_prompt.format(canvas_state=canvas_desc)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]
        tool_defs = self.registry.get_tool_definitions()

        all_code: list[str] = []
        all_desc: list[str] = []
        all_tool_calls: list[dict] = []
        last_content = ""
        round_num = 0

        await self._dispatch_event(EventType.AGENT_START, {"run_id": run_id, "message": message})

        for round_num in range(1, self.max_rounds + 1):
            plan_result = await self._plan(messages, tool_defs)
            if plan_result is None:
                break

            response, error = plan_result
            if error:
                await self._dispatch_event(EventType.AGENT_ERROR, {"run_id": run_id, "error": error})
                return AgentResponse(
                    run_id=run_id,
                    success=False,
                    error=error,
                    content=last_content,
                    code="\n".join(all_code),
                    description="\n".join(all_desc),
                    rounds=round_num - 1,
                )

            last_content = response.content or last_content

            if not response.tool_calls:
                break

            observe_msg = self._observe(response)
            act_results = await self._act(response.tool_calls)

            for tr in act_results:
                if not tr["is_error"]:
                    if tr["code"]:
                        all_code.append(tr["code"])
                    if tr["description"]:
                        all_desc.append(tr["description"])
                all_tool_calls.append({
                    "name": tr["name"],
                    "arguments": tr["arguments"],
                    "is_error": tr["is_error"],
                    "status": tr["status"].value,
                })

            messages.append({
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [tc.to_dict() for tc in response.tool_calls],
            })

            tool_result_msgs = []
            for tr in act_results:
                tool_result_msgs.append({
                    "tool_call_id": tr["tool_call_id"],
                    "role": "tool",
                    "content": json.dumps({
                        "is_error": tr["is_error"],
                        "status": tr["status"].value,
                        "code": tr["code"],
                        "description": tr["description"],
                        "error": tr["error"],
                    }, ensure_ascii=False),
                })
            messages.extend(tool_result_msgs)

            messages.append({"role": "user", "content": observe_msg})

        await self._dispatch_event(EventType.AGENT_STOP, {"run_id": run_id, "rounds": round_num})

        return AgentResponse(
            run_id=run_id,
            content=last_content,
            code="\n".join(all_code),
            description="\n".join(all_desc),
            tool_calls=all_tool_calls,
            rounds=round_num,
        )

    async def _plan(self, messages: list[dict], tools: list[dict]) -> tuple[LLMResponse, str | None] | None:
        try:
            response = await self.provider.achat(
                messages=messages,
                tools=tools if tools else None,
            )
            return response, None
        except Exception as e:
            return None, f"LLM call failed: {e}"

    def _observe(self, response: LLMResponse) -> str:
        parts = []
        if response.content:
            parts.append(f"Assistant said: {response.content}")
        for tc in response.tool_calls:
            parts.append(f"Tool call: {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})")
        return "\n".join(parts) if parts else "No response"

    async def _act(self, tool_calls: list[ToolCall]) -> list[dict]:
        results = []
        for tc in tool_calls:
            await self._dispatch_event(EventType.TOOL_CALL, {"name": tc.name, "arguments": tc.arguments})

            result = self.registry.execute(tc.name, **tc.arguments)
            status = classify_tool_error(result, tc.name, self.registry)

            await self._dispatch_event(
                EventType.TOOL_RESULT if not result.is_error else EventType.TOOL_ERROR,
                {"name": tc.name, "is_error": result.is_error, "status": status.value, "error": result.error},
            )

            results.append({
                "tool_call_id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
                "is_error": result.is_error,
                "status": status,
                "code": result.code,
                "description": result.description,
                "error": result.error,
            })
        return results

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

    async def _dispatch_event(self, event_type: EventType, data: dict):
        await self.event_bus.dispatch(BaseEvent(event_type, data))

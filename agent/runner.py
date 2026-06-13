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
from traces import DaemonTracer
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
    et = result.error_type
    if et == "not_found":
        return ToolResultStatus.FAILED_NOT_FOUND
    if et == "invalid_args":
        return ToolResultStatus.FAILED_INVALID_ARGS
    if et == "permission_denied" or et == "execution_error":
        return ToolResultStatus.FAILED_EXECUTION
    if et == "exception":
        return ToolResultStatus.FAILED_UNKNOWN
    # fallback: string matching (backward compat)
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
    new_messages: list[dict] = field(default_factory=list)

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
    tracer: DaemonTracer | None = None
    system_prompt: str = SYSTEM_PROMPT
    max_rounds: int = 5


class AgentRunner:
    def __init__(self, config: AgentConfig | None = None):
        config = config or AgentConfig()
        self.provider = config.provider
        self.registry = config.registry or ToolRegistry()
        self.event_bus = config.event_bus or EventBus()
        self.tracer = config.tracer
        self.system_prompt = config.system_prompt
        self.max_rounds = config.max_rounds

        if not self.provider:
            raise ValueError("LLMProvider is required")
        if self.tracer:
            from providers.tracing_provider import TracingProvider
            self.provider = TracingProvider(inner=self.provider, tracer=self.tracer)

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

    async def run(self, message: str, canvas_state: dict | None = None,
                   run_id: str | None = None,
                   history: list[dict] | None = None,
                   session: dict | None = None,
                   store=None) -> AgentResponse:
        if run_id is None:
            run_id = new_run_id()
        canvas_state = canvas_state or {}
        canvas_desc = self._format_canvas_state(canvas_state)

        # ── restore context from session store ─────────────────────────
        if store is not None:
            history = store.read_messages()  # full thread replay, no truncation

        # ── inject notes into system prompt ────────────────────────────
        notes = store.read_notes() if store is not None else ""
        system_prompt = self.system_prompt.format(canvas_state=canvas_desc)
        if notes:
            system_prompt += "\n\nRemembered facts:\n" + notes

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})
        tool_defs = self.registry.get_tool_definitions()

        all_code: list[str] = []
        all_desc: list[str] = []
        all_tool_calls: list[dict] = []
        new_messages: list[dict] = []
        last_content = ""
        round_num = 0

        await self._dispatch_event(EventType.AGENT_START, {"message": message}, run_id=run_id)

        for round_num in range(1, self.max_rounds + 1):
            plan_result = await self._plan(messages, tool_defs, run_id=run_id, round_num=round_num)
            if plan_result is None:
                break

            response, error = plan_result
            if error:
                await self._dispatch_event(EventType.AGENT_ERROR, {"error": error}, run_id=run_id)
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
            act_results = await self._act(response.tool_calls, run_id=run_id, round_num=round_num)

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

            assistant_msg = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [tc.to_dict() for tc in response.tool_calls],
            }
            messages.append(assistant_msg)
            new_messages.append(assistant_msg)

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
            new_messages.extend(tool_result_msgs)

            messages.append({"role": "user", "content": observe_msg})

        # ── persist final assistant answer ──────────────────────────────
        if last_content:
            final_msg = {"role": "assistant", "content": last_content}
            messages.append(final_msg)
            new_messages.append(final_msg)

        await self._dispatch_event(EventType.AGENT_STOP, {"rounds": round_num}, run_id=run_id)

        return AgentResponse(
            run_id=run_id,
            content=last_content,
            code="\n".join(all_code),
            description="\n".join(all_desc),
            tool_calls=all_tool_calls,
            rounds=round_num,
            new_messages=new_messages,
        )

    async def _plan(self, messages: list[dict], tools: list[dict], run_id: str = "", round_num: int = 0) -> tuple[LLMResponse, str | None] | None:
        model = getattr(self.provider, "model", "")
        tool_count = len(tools) if tools else 0

        await self._dispatch_event(EventType.LLM_REQUEST, {
            "model": model,
            "message_count": len(messages),
            "tool_count": tool_count,
        }, run_id=run_id, round_num=round_num)

        t0 = time.time()

        async def _call_llm():
            return await self.provider.achat(
                messages=messages,
                tools=tools if tools else None,
                step=round_num,
            )

        try:
            from .retry import retry_with_backoff

            async def _on_retry(attempt: int, delay: float, err: str):
                await self._dispatch_event(EventType.LLM_RETRY, {
                    "model": model,
                    "attempt": attempt + 1,
                    "delay_ms": round(delay * 1000),
                    "error": err[:200],
                }, run_id=run_id, round_num=round_num)

            response, attempts = await retry_with_backoff(
                _call_llm,
                max_retries=2,
                base_delay=1.0,
                on_retry=_on_retry,
            )
            latency_ms = round((time.time() - t0) * 1000, 1)

            content_snippet = (response.content or "")[:200]
            await self._dispatch_event(EventType.LLM_RESPONSE, {
                "model": model,
                "content": content_snippet,
                "tool_calls": len(response.tool_calls),
                "tokens_used": response.tokens_used,
                "latency_ms": latency_ms,
                "retries": attempts - 1,
            }, run_id=run_id, round_num=round_num)

            return response, None
        except Exception as e:
            latency_ms = round((time.time() - t0) * 1000, 1)
            await self._dispatch_event(EventType.LLM_ERROR, {
                "model": model,
                "error": str(e),
                "latency_ms": latency_ms,
            }, run_id=run_id, round_num=round_num)

            return None, f"LLM call failed: {e}"

    def _observe(self, response: LLMResponse) -> str:
        parts = []
        if response.content:
            parts.append(f"Assistant said: {response.content}")
        for tc in response.tool_calls:
            parts.append(f"Tool call: {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})")
        return "\n".join(parts) if parts else "No response"

    async def _act(self, tool_calls: list[ToolCall], run_id: str = "", round_num: int = 0) -> list[dict]:
        results = []
        for tc in tool_calls:
            await self._dispatch_event(EventType.TOOL_CALL, {
                "name": tc.name, "arguments": tc.arguments,
            }, run_id=run_id, round_num=round_num)

            t0 = time.time()
            result = self.registry.execute(tc.name, **tc.arguments)
            status = classify_tool_error(result, tc.name, self.registry)
            latency_ms = round((time.time() - t0) * 1000, 1)

            # pick the right event type based on error_type
            if result.error_type == "permission_denied":
                event_type = EventType.TOOL_BLOCKED
            elif result.error_type == "invalid_args":
                event_type = EventType.TOOL_VALIDATION_ERROR
            else:
                event_type = EventType.TOOL_RESULT if not result.is_error else EventType.TOOL_ERROR

            await self._dispatch_event(
                event_type,
                {
                    "name": tc.name,
                    "is_error": result.is_error,
                    "error_type": result.error_type,
                    "status": status.value,
                    "latency_ms": latency_ms,
                    "error": result.error,
                },
                run_id=run_id, round_num=round_num,
            )

            results.append({
                "tool_call_id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
                "is_error": result.is_error,
                "status": status,
                "error_type": result.error_type,
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

    async def _dispatch_event(self, event_type: EventType, data: dict, run_id: str = "", round_num: int = 0):
        payload = dict(data)
        payload.setdefault("run_id", run_id)
        if round_num:
            payload["round"] = round_num
        await self.event_bus.dispatch(BaseEvent(event_type, payload, run_id=run_id))

    def _build_task_registry(self, task_manager):
        """Register the 4 task-planning tools into the current registry."""
        from agent.task_tools import TASK_TOOLS
        for tool_cls in TASK_TOOLS:
            self.registry.register(tool_cls(task_manager))

    def _build_session_tools(self, store):
        """Register session-aware tools (e.g. note_save)."""
        from agent.session_tools import SESSION_TOOLS
        for tool_cls in SESSION_TOOLS:
            self.registry.register(tool_cls(store))

    async def run_and_capture(self, message: str, canvas_state: dict | None = None,
                               run_id: str | None = None,
                               history: list[dict] | None = None,
                               session: dict | None = None,
                               store=None) -> AgentResponse:
        """Run with task-planning tools enabled.

        Creates a ``TaskManager`` bound to this run's ``.tasks/``
        directory and registers the 4 task tools (create / update /
        list / get) into the tool registry so the LLM can autonomously
        plan and track its own tasks.

        When ``session`` and ``store`` are provided, context is restored
        from the session (full thread replay + notes injection) and
        session-aware tools (note_save) are registered.
        """
        if run_id is None:
            run_id = new_run_id()

        from agent.task_manager import TaskManager
        task_manager = TaskManager(run_id)
        self._build_task_registry(task_manager)

        if store is not None:
            self._build_session_tools(store)

        from .prompts import PLANNING_SYSTEM_PROMPT
        self.system_prompt = PLANNING_SYSTEM_PROMPT

        return await self.run(message=message, canvas_state=canvas_state,
                              run_id=run_id, history=history,
                              session=session, store=store)

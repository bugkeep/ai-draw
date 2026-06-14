import json
import time
import random
import string
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from providers.base import LLMProvider, LLMResponse, ToolCall
from tools.base import ToolResult
from tools.registry import ToolRegistry
from traces import DaemonTracer
from events import EventBus, EventType, BaseEvent
from .prompts import BASE_SYSTEM_PROMPT, SOFTWARE_OPERATION_PROMPT
from .context import format_three_layer_context
from agent.router import DrawingModeRouter
from .prompts import get_mode_prompt


COMPLEX_SCENE_MIN_DRAW_CALLS = 6
DETAILED_COMPOSITION_MIN_ELEMENTS = 10
COMPLEX_SCENE_MIN_ROUNDS = 12
DRAWING_TOOL_PREFIX = "draw_"
CONCENTRIC_CIRCLE_RE = re.compile(
    r"(?:同心圆|同星圆|同圆心|套圆|套着的圆|靶心|concentric|bullseye)",
    re.IGNORECASE,
)

CN_COLOR_MAP = {
    "红色": "red",
    "红": "red",
    "蓝色": "blue",
    "蓝": "blue",
    "绿色": "green",
    "绿": "green",
    "黄色": "yellow",
    "黄": "yellow",
    "紫色": "purple",
    "紫": "purple",
    "橙色": "orange",
    "橙": "orange",
    "粉色": "pink",
    "粉": "pink",
    "黑色": "black",
    "黑": "black",
    "白色": "white",
    "白": "white",
    "灰色": "gray",
    "灰": "gray",
    "青色": "cyan",
    "青": "cyan",
    "棕色": "brown",
    "棕": "brown",
}

COLOR_RE = "|".join(sorted(map(re.escape, CN_COLOR_MAP), key=len, reverse=True))


def new_run_id() -> str:
    t = time.localtime()
    ts = time.strftime("%y%m%d-%H%M%S", t)
    rand = "".join(random.choices(string.digits, k=6))
    return f"{ts}-{rand}"


class ToolResultStatus(Enum):
    SUCCESS = "success"
    FAILED_INVALID_ARGS = "failed_invalid_args"
    FAILED_NOT_FOUND = "failed_not_found"
    FAILED_PERMISSION = "failed_permission"
    FAILED_TIMEOUT = "failed_timeout"
    FAILED_RATE_LIMITED = "failed_rate_limited"
    FAILED_EXECUTION = "failed_execution"
    FAILED_UNKNOWN = "failed_unknown"


def classify_tool_error(result: ToolResult, tool_name: str, registry: ToolRegistry) -> ToolResultStatus:
    if not result.is_error:
        return ToolResultStatus.SUCCESS
    et = result.error_type
    if et == "invalid_args":
        return ToolResultStatus.FAILED_INVALID_ARGS
    if et == "not_found":
        return ToolResultStatus.FAILED_NOT_FOUND
    if et == "permission_denied":
        return ToolResultStatus.FAILED_PERMISSION
    if et == "timeout":
        return ToolResultStatus.FAILED_TIMEOUT
    if et == "rate_limited":
        return ToolResultStatus.FAILED_RATE_LIMITED
    if et in ("execution_error", "exception"):
        return ToolResultStatus.FAILED_EXECUTION
    # fallback: string matching
    error = result.error.lower()
    if "timed out" in error or "timeout" in error:
        return ToolResultStatus.FAILED_TIMEOUT
    if "rate limit" in error or "too many requests" in error or "429" in error:
        return ToolResultStatus.FAILED_RATE_LIMITED
    if "unknown tool" in error:
        return ToolResultStatus.FAILED_NOT_FOUND
    if "required" in error or "missing" in error or "invalid" in error:
        return ToolResultStatus.FAILED_INVALID_ARGS
    if "permission" in error or "not allowed" in error:
        return ToolResultStatus.FAILED_PERMISSION
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
    system_prompt: str = ""  # defaults to BASE_SYSTEM_PROMPT in __init__
    max_rounds: int = 5
    compact_threshold: float = 0.80     # context_pct triggers auto-compact
    compact_keep_rounds: int = 3         # rounds to preserve after compact
    router: DrawingModeRouter | None = None


class AgentRunner:
    def __init__(self, config: AgentConfig | None = None):
        config = config or AgentConfig()
        self.provider = config.provider
        self.registry = config.registry or ToolRegistry()
        self.event_bus = config.event_bus or EventBus()
        self.tracer = config.tracer
        self.system_prompt = config.system_prompt or BASE_SYSTEM_PROMPT
        self.max_rounds = config.max_rounds

        self.compact_threshold = config.compact_threshold
        self.compact_keep_rounds = config.compact_keep_rounds
        self.router = config.router or DrawingModeRouter()

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

        # ── restore context from session store (tool results truncated) ─
        if store is not None:
            history = store.read_messages(truncate_tool_result=True)

        # ── intent routing ────────────────────────────────────────────
        route = await self.router.route(message, canvas_state, history)
        await self._dispatch_event(EventType.ROUTING_RESULT, {
            "mode": route.mode.value,
            "confidence": route.confidence,
            "subject": route.subject,
            "reason": route.reason,
            "requires_search": route.requires_search,
        }, run_id=run_id)
        mode_prompt = get_mode_prompt(route.mode.value)

        # ── three-layer context injection ──────────────────────────────
        three_layer = format_three_layer_context(store)
        system_prompt = self.system_prompt.format(
            canvas_state=canvas_desc,
            mode_prompt=mode_prompt,
            operation_prompt=SOFTWARE_OPERATION_PROMPT,
        )
        if three_layer:
            system_prompt += "\n\n" + three_layer

        direct_response = await self._try_direct_geometry(
            message=message,
            run_id=run_id,
        )
        if direct_response is not None:
            return direct_response

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
        ]
        if history:
            messages.extend(history)
        # Don't duplicate the user message when store already wrote it
        if not (history and history[-1].get("role") == "user"
                and history[-1].get("content") == message):
            messages.append({"role": "user", "content": message})
        tool_defs = self.registry.get_tool_definitions()

        all_code: list[str] = []
        all_desc: list[str] = []
        all_tool_calls: list[dict] = []
        new_messages: list[dict] = []
        last_content = ""
        round_num = 0
        round_limit = self.max_rounds
        if route.mode.value == "image_generation":
            round_limit = max(round_limit, COMPLEX_SCENE_MIN_ROUNDS)

        await self._dispatch_event(EventType.AGENT_START, {"message": message}, run_id=run_id)

        for round_num in range(1, round_limit + 1):
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

            # ── context watermark ──────────────────────────────────────
            if response.context_pct > 0:
                await self._dispatch_event(EventType.CONTEXT_WATERMARK, {
                    "pct": response.context_pct,
                    "tokens_used": response.tokens_used,
                    "context_window": response.context_window,
                }, run_id=run_id, round_num=round_num)

                if response.context_pct >= self.compact_threshold:
                    before = len(messages)
                    messages = self._compact_messages(messages)
                    after = len(messages)
                    await self._dispatch_event(EventType.CONTEXT_COMPACTED, {
                        "pct": response.context_pct,
                        "before": before,
                        "after": after,
                        "removed": before - after,
                    }, run_id=run_id, round_num=round_num)

            last_content = response.content or last_content

            if not response.tool_calls:
                if self._complex_scene_incomplete(route.mode.value, all_tool_calls, message):
                    assistant_msg = {
                        "role": "assistant",
                        "content": response.content or "",
                    }
                    continuation_msg = {
                        "role": "user",
                        "content": (
                            "The complex editable scene is not complete yet. "
                            f"Original user request: {message}\n"
                            "Continue drawing the missing layers, named subjects, "
                            "spatial relationships, and visual details. Use several "
                            "drawing tool calls in this response, and only finish "
                            "after the original subject and its required structure "
                            "are visibly complete."
                        ),
                    }
                    messages.extend([assistant_msg, continuation_msg])
                    new_messages.extend([assistant_msg, continuation_msg])
                    continue
                break

            observe_msg = self._observe(response)
            act_results = await self._act(response.tool_calls, run_id=run_id, round_num=round_num)

            for tr in act_results:
                if not tr["is_error"]:
                    if tr["code"]:
                        all_code.append(f"{{\n{tr['code']}\n}}")
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

    @staticmethod
    def _build_concentric_circle_args(message: str) -> dict | None:
        if not CONCENTRIC_CIRCLE_RE.search(message):
            return None

        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", message)]
        args: dict[str, Any] = {
            "center_x": 400,
            "center_y": 300,
            "outer_radius": 120,
            "inner_radius": 60,
            "outer_color": "blue",
            "inner_color": "green",
            "object_id": "concentric_circles",
        }

        center_match = re.search(
            r"(?:圆心|中心)\s*[在处]?\s*[\(（]?\s*(\d+(?:\.\d+)?)\s*[,，\s]\s*(\d+(?:\.\d+)?)",
            message,
        )
        if center_match:
            args["center_x"] = float(center_match.group(1))
            args["center_y"] = float(center_match.group(2))

        outer_match = re.search(r"(?:外层|外圈|外面|外边)[^，。,；;]*?(%s)" % COLOR_RE, message)
        inner_match = re.search(r"(?:最里面|里面|内层|内圈|中心|中间)[^，。,；;]*?(%s)" % COLOR_RE, message)
        if outer_match:
            args["outer_color"] = CN_COLOR_MAP[outer_match.group(1)]
        if inner_match:
            args["inner_color"] = CN_COLOR_MAP[inner_match.group(1)]

        # Common spoken order: "最里面是绿色，外层是蓝色". If role-specific
        # parsing failed, infer from the first two colors in the sentence.
        color_hits = re.findall(COLOR_RE, message)
        if len(color_hits) >= 2 and not inner_match and not outer_match:
            args["inner_color"] = CN_COLOR_MAP[color_hits[0]]
            args["outer_color"] = CN_COLOR_MAP[color_hits[1]]

        radius_match = re.search(r"(?:外层|外圈|外面|外边).*?(?:半径|r)\s*[:：=]?\s*(\d+(?:\.\d+)?)", message, re.IGNORECASE)
        if radius_match:
            args["outer_radius"] = float(radius_match.group(1))
        radius_match = re.search(r"(?:最里面|里面|内层|内圈|中心|中间).*?(?:半径|r)\s*[:：=]?\s*(\d+(?:\.\d+)?)", message, re.IGNORECASE)
        if radius_match:
            args["inner_radius"] = float(radius_match.group(1))
        elif len(nums) >= 1 and "半径" in message and args["outer_radius"] == 120:
            args["outer_radius"] = nums[-1]
            args["inner_radius"] = max(nums[-1] / 2, 1)

        return args

    async def _try_direct_geometry(self, message: str, run_id: str) -> AgentResponse | None:
        args = self._build_concentric_circle_args(message)
        if args is None:
            return None

        await self._dispatch_event(EventType.AGENT_START, {"message": message}, run_id=run_id)
        tool_call = ToolCall(
            id=f"direct_{run_id}",
            name="draw_concentric_circles",
            arguments=args,
        )
        act_results = await self._act([tool_call], run_id=run_id, round_num=1)

        all_code = []
        all_desc = []
        tool_calls = []
        for tr in act_results:
            if not tr["is_error"]:
                if tr["code"]:
                    all_code.append(f"{{\n{tr['code']}\n}}")
                if tr["description"]:
                    all_desc.append(tr["description"])
            tool_calls.append({
                "name": tr["name"],
                "arguments": tr["arguments"],
                "is_error": tr["is_error"],
                "status": tr["status"].value,
            })

        await self._dispatch_event(EventType.AGENT_STOP, {"rounds": 1}, run_id=run_id)
        failed = next((tr for tr in act_results if tr["is_error"]), None)
        return AgentResponse(
            run_id=run_id,
            content="已画好同心圆，所有圆都共用同一个圆心。",
            code="\n".join(all_code),
            description="\n".join(all_desc),
            tool_calls=tool_calls,
            success=failed is None,
            error=failed["error"] if failed else "",
            rounds=1,
            new_messages=[
                {"role": "assistant", "content": "已画好同心圆，所有圆都共用同一个圆心。"}
            ],
        )

    @staticmethod
    def _complex_scene_incomplete(mode: str, tool_calls: list[dict], message: str = "") -> bool:
        if mode != "image_generation":
            return False
        if any(AgentRunner._is_detailed_subject_tool(call) for call in tool_calls):
            return False
        if AgentRunner._missing_subject_requirements(message, tool_calls):
            return True
        successful_draw_calls = sum(
            1 for call in tool_calls
            if not call.get("is_error")
            and str(call.get("name", "")).startswith(DRAWING_TOOL_PREFIX)
        )
        return successful_draw_calls < COMPLEX_SCENE_MIN_DRAW_CALLS

    @staticmethod
    def _missing_subject_requirements(message: str, tool_calls: list[dict]) -> list[str]:
        if not re.search(r"(?:汽车|轿车|跑车|车辆|\bcar\b|\bvehicle\b)", message, re.IGNORECASE):
            return []

        labels = " ".join(
            f"{call.get('name', '')} "
            f"{call.get('arguments', {}).get('object_id', '')} "
            f"{call.get('arguments', {}).get('semantic_type', '')}"
            for call in tool_calls
            if not call.get("is_error")
        ).lower()
        requirements = {
            "vehicle body": ("car_body", "vehicle_body", "body", "chassis"),
            "wheels": ("wheel", "tire", "tyre"),
            "windows": ("window", "windshield", "windscreen", "glass"),
            "depth lighting": ("shadow", "highlight", "reflection"),
        }
        return [
            name for name, alternatives in requirements.items()
            if not any(alternative in labels for alternative in alternatives)
        ]

    @staticmethod
    def _is_detailed_vector_composition(call: dict) -> bool:
        if call.get("is_error") or call.get("name") != "draw_vector_composition":
            return False
        svg = str(call.get("arguments", {}).get("svg", ""))
        visible_elements = re.findall(
            r"<(?:path|circle|ellipse|rect|line|polyline|polygon)\b",
            svg,
            re.IGNORECASE,
        )
        return len(visible_elements) >= DETAILED_COMPOSITION_MIN_ELEMENTS

    @staticmethod
    def _is_detailed_subject_tool(call: dict) -> bool:
        if call.get("is_error"):
            return False
        if call.get("name") == "draw_perspective_vehicle":
            return True
        return AgentRunner._is_detailed_vector_composition(call)

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
            tool = self.registry.get(tc.name)

            # ── 0. tool not found ─────────────────────────────────────────
            if tool is None:
                await self._dispatch_event(EventType.TOOL_ERROR, {
                    "name": tc.name, "is_error": True,
                    "error_type": "not_found", "status": "failed_not_found",
                    "error": f"Unknown tool: {tc.name}",
                }, run_id=run_id, round_num=round_num)
                results.append({
                    "tool_call_id": tc.id,
                    "name": tc.name, "arguments": tc.arguments,
                    "is_error": True, "status": ToolResultStatus.FAILED_NOT_FOUND,
                    "error_type": "not_found", "code": "", "description": "",
                    "error": f"Unknown tool: {tc.name}",
                })
                continue

            # ── 1. Pydantic parameter validation ──────────────────────────
            try:
                validated = tool.validate_params(tc.arguments)
            except Exception as e:
                await self._dispatch_event(EventType.TOOL_VALIDATION_ERROR, {
                    "name": tc.name, "is_error": True,
                    "error_type": "invalid_args",
                    "status": "failed_invalid_args",
                    "error": str(e),
                }, run_id=run_id, round_num=round_num)
                results.append({
                    "tool_call_id": tc.id,
                    "name": tc.name, "arguments": tc.arguments,
                    "is_error": True, "status": ToolResultStatus.FAILED_INVALID_ARGS,
                    "error_type": "invalid_args", "code": "", "description": "",
                    "error": str(e),
                })
                continue

            # ── 2. Permission check (may wait for user) ────────────────────
            perms = self.registry.permissions
            approved, req_id = await perms.check_and_wait(tc.name, validated)

            if not approved:
                await self._dispatch_event(EventType.TOOL_BLOCKED, {
                    "name": tc.name, "is_error": True,
                    "error_type": "permission_denied",
                    "status": "failed_permission",
                    "error": f"Permission denied: '{tc.name}'",
                }, run_id=run_id, round_num=round_num)
                results.append({
                    "tool_call_id": tc.id,
                    "name": tc.name, "arguments": tc.arguments,
                    "is_error": True, "status": ToolResultStatus.FAILED_PERMISSION,
                    "error_type": "permission_denied", "code": "", "description": "",
                    "error": f"Permission denied: '{tc.name}'",
                })
                continue

            # ── 3. Execute tool ───────────────────────────────────────────
            await self._dispatch_event(EventType.TOOL_CALL, {
                "name": tc.name, "arguments": tc.arguments,
            }, run_id=run_id, round_num=round_num)

            t0 = time.time()
            result = self.registry.execute(tc.name, **tc.arguments)
            status = classify_tool_error(result, tc.name, self.registry)
            latency_ms = round((time.time() - t0) * 1000, 1)

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
            oid = obj.get("object_id", "")
            id_part = f" id={oid}" if oid else ""
            parts.append(f"  [{i}] {obj_type} at ({left},{top}) fill={fill}{id_part}")
        return f"{len(objects)} objects:\n" + "\n".join(parts)

    def _compact_messages(self, messages: list[dict],
                           keep_rounds: int | None = None) -> list[dict]:
        """Compact in-memory messages when context watermark is high.

        Preserves: system prompt, the last ``keep_rounds`` complete turns,
        and the most recent user message.  Middle content is replaced with
        a compact placeholder so the LLM retains awareness of prior work
        without paying full token cost.

        ``keep_rounds`` defaults to ``self.compact_keep_rounds``.
        """
        if keep_rounds is None:
            keep_rounds = self.compact_keep_rounds

        if len(messages) <= 3:
            return messages

        system = messages[0]
        current = messages[-1]

        # Walk backward from messages[-2] to find last N complete rounds.
        # A round ends with a tool-role message and is followed by a user-role
        # observe (or the current message).  We count backwards:
        #   - each assistant msg + its tool results + following observe = 1 round
        # The last element in messages[-1] is already the current observe/user.
        keep_count = 2  # at least the last assistant + 1 tool
        for i in range(len(messages) - 2, 0, -1):
            if messages[i].get("role") == "assistant":
                # Count messages from this assistant back to previous
                # assistant or system, then break if we've collected enough
                # rounds.
                # Simple approach: keep the last keep_rounds * 3 messages
                # (each round ≈ assistant + at least 1 tool + 1 observe/user)
                # plus the final current message.
                round_size = 3  # assistant + tool + observe
                keep_count = max(keep_count, keep_rounds * round_size)
                break

        if keep_count >= len(messages) - 1:
            return messages

        before = messages[1:len(messages) - keep_count - 1]
        after = messages[len(messages) - keep_count - 1:-1]

        compact_msg = {
            "role": "user",
            "content": (
                f"[Previous conversation compacted: {len(before)} messages "
                f"from earlier rounds omitted to fit context window. "
                f"All prior work is complete. Continuing from below.]"
            ),
        }

        return [system] + [compact_msg] + after + [current]

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

    def _build_skill_registry(self, skill, parent_runner=None):
        """Replace the tool registry with one restricted to the skill's whitelist.

        Also registers ``spawn_agent`` so the agent can delegate work to
        sub-agents.  The permission policy's ``allow_only`` is set to
        the skill's tool list so that even if a tool somehow leaks in, it
        is blocked at the policy level.
        """
        allowed = set(skill.tools or [])
        new_registry = ToolRegistry()
        new_registry.permissions = self.registry.permissions

        # lock policy to the skill's whitelist
        if new_registry.permissions and new_registry.permissions.policy:
            new_registry.permissions.policy.allow_only = list(allowed)

        for name in allowed:
            if name == "spawn_agent":
                from agent.sub_agent import SpawnAgentTool
                parent = parent_runner or self
                new_registry.register(SpawnAgentTool(parent))
            else:
                tool = self.registry.get(name)
                if tool is not None:
                    new_registry.register(tool.__class__())

        self.registry = new_registry

    async def run_with_skill(self, message: str, skill,
                              canvas_state: dict | None = None,
                              run_id: str | None = None,
                              store=None) -> AgentResponse:
        """Run with a skill's prompt and restricted tool registry."""
        self.system_prompt = skill.prompt
        self._build_skill_registry(skill)
        return await self.run(
            message=message,
            canvas_state=canvas_state,
            run_id=run_id,
            store=store,
        )

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

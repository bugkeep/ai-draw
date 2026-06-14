import json
import time
from events import EventBus, EventType, BaseEvent
from tools.registry import ToolRegistry
from tools.base import ToolResult
from agent.runner import AgentRunner, AgentConfig, new_run_id, classify_tool_error


class SubAgentRunner:
    """A lightweight, isolated agent run for sub-tasks.

    ``SubAgentRunner`` creates a fresh ``AgentRunner`` with a restricted
    tool registry (only the tools named in ``allowed_tools``) and runs a
    single turn (plan + optional act).  Events are dispatched to the same
    ``event_bus`` as the parent, so they flow to the frontend naturally.

    The result is returned as a dict suitable for the parent agent's tool
    result message.
    """

    def __init__(self, parent_runner: AgentRunner):
        self._parent = parent_runner

    async def run(
        self,
        role: str,
        role_prompt: str,
        input_text: str,
        allowed_tools: list[str] | None = None,
        max_rounds: int = 3,
    ) -> dict:
        sub_run_id = new_run_id()

        # ── build restricted registry ──────────────────────────────────
        registry = ToolRegistry()
        registry.permissions = self._parent.registry.permissions

        allowed = set(allowed_tools or [])
        for name in allowed:
            tool = self._parent.registry.get(name)
            if tool is not None:
                registry.register(tool.__class__())
            elif name == "spawn_agent":
                registry.register(SpawnAgentTool(self._parent))

        # ── build sub-agent runner ─────────────────────────────────────
        config = AgentConfig(
            provider=self._parent.provider,
            registry=registry,
            event_bus=self._parent.event_bus,
            system_prompt=role_prompt,
            max_rounds=max_rounds,
        )
        runner = AgentRunner(config)

        # ── dispatch start event ───────────────────────────────────────
        await self._parent._dispatch_event(
            EventType.SUB_AGENT_START, {
                "sub_run_id": sub_run_id,
                "role": role,
                "input": input_text,
                "allowed_tools": list(allowed),
            }, run_id=sub_run_id,
        )

        # ── run ────────────────────────────────────────────────────────
        t0 = time.time()
        result = await runner.run(input_text, run_id=sub_run_id)
        elapsed = round((time.time() - t0) * 1000, 1)

        # ── dispatch stop event ────────────────────────────────────────
        await self._parent._dispatch_event(
            EventType.SUB_AGENT_STOP, {
                "sub_run_id": sub_run_id,
                "role": role,
                "rounds": result.rounds,
                "latency_ms": elapsed,
                "success": result.success,
            }, run_id=sub_run_id,
        )

        return {
            "sub_run_id": sub_run_id,
            "role": role,
            "content": result.content,
            "code": result.code,
            "description": result.description,
            "tool_calls": result.tool_calls,
            "rounds": result.rounds,
            "success": result.success,
            "error": result.error,
        }


class SpawnAgentTool:
    """Tool that lets the parent agent spawn an isolated sub-agent.

    Registered as ``spawn_agent`` in the parent's tool registry.
    """

    def __init__(self, runner: AgentRunner):
        self._runner = runner
        self._sub_runner = SubAgentRunner(runner)

    def definition(self):
        from tools.base import ToolDefinition, ToolParameter
        return ToolDefinition(
            name="spawn_agent",
            description=(
                "Spawn an isolated sub-agent to perform a specific role. "
                "The sub-agent gets its own restricted tool set and message "
                "history.  Returns the sub-agent's final answer and any "
                "code/description it produced."
            ),
            parameters=[
                ToolParameter(
                    name="role",
                    type="string",
                    description="Role name for the sub-agent (e.g. planner, executor, reviewer)",
                    required=True,
                ),
                ToolParameter(
                    name="prompt",
                    type="string",
                    description="System prompt / instructions for the sub-agent",
                    required=True,
                ),
                ToolParameter(
                    name="input",
                    type="string",
                    description="The input or question for the sub-agent to process",
                    required=True,
                ),
                ToolParameter(
                    name="tools",
                    type="string",
                    description=(
                        "Comma-separated list of tool names the sub-agent "
                        "is allowed to use (e.g. 'draw_circle,draw_rect')"
                    ),
                    required=True,
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        """Execute is sync, but the actual work is async.

        This method creates an asyncio task and blocks on it.
        """
        import asyncio
        try:
            result = asyncio.run(self._async_execute(**kwargs))
            return result
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(self._async_execute(**kwargs))
                return result
            finally:
                loop.close()

    async def _async_execute(self, **kwargs) -> ToolResult:
        role = kwargs.get("role", "")
        role_prompt = kwargs.get("prompt", "")
        input_text = kwargs.get("input", "")
        tools_str = kwargs.get("tools", "")
        allowed = [t.strip() for t in tools_str.split(",") if t.strip()]

        if not role or not role_prompt or not input_text:
            return ToolResult(
                is_error=True,
                error="role, prompt, and input are required",
                error_type="invalid_args",
            )

        result = await self._sub_runner.run(
            role=role,
            role_prompt=role_prompt,
            input_text=input_text,
            allowed_tools=allowed,
        )

        if result["success"]:
            return ToolResult(
                is_error=False,
                description=(
                    f"[Sub-agent '{role}']: {result['content'][:200]}"
                ),
                data=result,
            )
        else:
            return ToolResult(
                is_error=True,
                error=f"Sub-agent '{role}' failed: {result['error']}",
                error_type="execution_error",
                data=result,
            )

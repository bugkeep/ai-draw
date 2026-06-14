import os
import json
import asyncio
from events import EventBus, BaseEvent, format_event_push
from traces import DaemonTracer


RUNS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))


def events_file(run_id: str) -> str:
    """Return the path to the events.jsonl for a given run."""
    return os.path.join(RUNS_DIR, run_id, "events.jsonl")


async def _replay_events(run_id: str, writer: asyncio.StreamWriter) -> int:
    """Replay historical events from a run's events.jsonl to a writer.

    Reads the JSONL file line by line, writes each as a newline-delimited
    message to the writer, then drains.  Returns the number of replayed
    events, or 0 if the file does not exist.
    """
    filepath = events_file(run_id)
    if not os.path.isfile(filepath):
        return 0

    count = 0
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            writer.write((stripped + "\n").encode())
            count += 1

    await writer.drain()
    return count


class JsonlRecorder:
    """Persists events to runs/<run_id>/events.jsonl as they are dispatched."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        event_bus.register_global(self._on_event)

    async def _on_event(self, event: BaseEvent):
        run_id = event.run_id or event.data.get("run_id", "")
        if not run_id:
            return

        filepath = events_file(run_id)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        line = format_event_push(event)

        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class TraceHandler:
    """EventBus subscriber that forwards all events to DaemonTracer.

    Registered as a global subscriber alongside JsonlRecorder and
    EventBroadcaster — EventBus itself has no knowledge of the trace
    system.
    """

    def __init__(self, event_bus: EventBus, tracer: DaemonTracer):
        self.tracer = tracer
        event_bus.register_global(self._on_event)

    async def _on_event(self, event: BaseEvent):
        await self.tracer.on_eventbus_event(event)


class AgentRunHandler:
    """Non-blocking agent run dispatcher.

    Generates a ``run_id`` in the handler (before the run starts), hands it
    to ``runner.run()`` so the frontend gets the id immediately, then
    kicks off the actual execution via ``asyncio.create_task()``.
    ``_running_runs`` tracks active tasks so shutdown can cancel them.
    """

    def __init__(self, daemon):
        self._daemon = daemon
        self._running_runs: dict[str, asyncio.Task] = {}

    async def handle_run(self, payload: dict) -> dict:
        from agent.runner import new_run_id

        message = payload.get("message", "")
        canvas_state = payload.get("canvas_state", {})
        provider = payload.get("provider", "openai")
        api_key = payload.get("api_key", "")

        run_id = new_run_id()
        self._daemon.init_runner(provider, api_key)

        task = asyncio.create_task(
            self._daemon._runner.run_and_capture(
                message=message,
                canvas_state=canvas_state,
                run_id=run_id,
            )
        )
        self._running_runs[run_id] = task
        task.add_done_callback(lambda _: self._running_runs.pop(run_id, None))

        return {"run_id": run_id}

    async def cancel_all_runs(self):
        for run_id, task in list(self._running_runs.items()):
            task.cancel()
        if self._running_runs:
            await asyncio.gather(*self._running_runs.values(), return_exceptions=True)
        self._running_runs.clear()


class SessionHandler:
    """Manages persistent conversation sessions.

    ``session.create`` returns a new ``session_id``.
    ``session.send_message`` injects past history into the agent run so the
    LLM sees the full conversation — not just the current turn.

    When the message starts with ``/``, the SkillLoader is consulted and the
    corresponding skill's prompt and tool whitelist are applied.
    """

    def __init__(self, daemon):
        self._daemon = daemon
        from agent.session_manager import SessionManager
        self._session_manager = SessionManager()
        from skills.loader import SkillLoader
        self._skill_loader = SkillLoader()
        self._running_runs: dict[str, asyncio.Task] = {}

    async def handle_create(self, payload: dict) -> dict:
        provider = payload.get("provider", "openai")
        api_key = payload.get("api_key", "")
        mode = payload.get("mode", "agent")
        title = payload.get("title", "")

        session_id = self._session_manager.create(
            mode=mode, title=title, provider=provider, api_key=api_key,
        )

        session_meta = self._session_manager.get_session(session_id)
        await self._daemon.event_bus.dispatch(
            BaseEvent(EventType.SESSION_CREATED, dict(session_meta),
                      run_id=session_id)
        )

        return {"session_id": session_id}

    async def handle_send_message(self, payload: dict) -> dict:
        session_id = payload.get("session_id", "")
        message = payload.get("message", "")

        if not session_id or not message:
            return {"error": "session_id and message are required"}

        # 1. write user message to thread first, then get context
        from agent.runner import new_run_id
        run_id = new_run_id()
        ctx = self._session_manager.send_message(
            session_id, message, run_id,
            skill_loader=self._skill_loader,
        )
        if "error" in ctx:
            return ctx

        session = ctx["session"]
        store = ctx["store"]
        skill = ctx.get("skill")
        skill_args = ctx.get("skill_args", "")

        # 2. init runner from session config
        provider = session.get("provider", "openai")
        api_key = session.get("api_key", "")
        self._daemon.init_runner(provider, api_key)

        # 3. start run with session context restored
        task = asyncio.create_task(
            self._run_with_session(message, session, store, run_id,
                                   skill=skill, skill_args=skill_args)
        )
        self._running_runs[run_id] = task
        task.add_done_callback(lambda _: self._running_runs.pop(run_id, None))

        return {"run_id": run_id}

    async def _run_with_session(self, message: str, session: dict,
                                 store, run_id: str,
                                 skill=None, skill_args: str = ""):
        try:
            if skill is not None:
                # dispatch skill.invoked event
                await self._daemon.event_bus.dispatch(
                    BaseEvent(EventType.SKILL_INVOKED, {
                        "command": skill.command,
                        "args": skill_args,
                        "tools": skill.tools,
                        "prompt_preview": skill.prompt[:200],
                    }, run_id=run_id)
                )
                # skill mode — use skill prompt + restricted registry
                self._daemon._runner._build_skill_registry(skill)
                self._daemon._runner.system_prompt = skill.prompt
                result = await self._daemon._runner.run(
                    message=skill_args or message,
                    run_id=run_id,
                    session=session,
                    store=store,
                )
            else:
                result = await self._daemon._runner.run_and_capture(
                    message=message,
                    run_id=run_id,
                    session=session,
                    store=store,
                )
            # persist only the new messages produced in this run
            store.append_messages(result.new_messages)
        except Exception as e:
            store.append_message("assistant", f"Error: {e}")

    async def cancel_all_runs(self):
        for run_id, task in list(self._running_runs.items()):
            task.cancel()
        if self._running_runs:
            await asyncio.gather(*self._running_runs.values(), return_exceptions=True)
        self._running_runs.clear()

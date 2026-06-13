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
            self._daemon._runner.run(
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

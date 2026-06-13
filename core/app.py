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

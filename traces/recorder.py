import os
import json
import time
import asyncio


TRACES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "traces"))
_SENTINEL = object()


class DaemonTracer:
    """System-wide timeline recorder with async drain.

    ``record()`` (called from sync and async contexts) only enqueues a
    dict to an ``asyncio.Queue`` -- O(1), no blocking.  A background
    ``_drain`` task continuously dequeues records and appends them to
    ``daemon.jsonl``, isolating file I/O from the main event loop.

    Call ``start()`` during daemon startup to spawn the drain task and
    ``await stop()`` during shutdown to flush remaining records.
    """

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(TRACES_DIR, "daemon.jsonl")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._drain_task: asyncio.Task | None = None

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self):
        """Spawn the background drain task (idempotent)."""
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain())

    async def stop(self):
        """Flush remaining records and stop the drain task."""
        if self._drain_task is None:
            return
        self._queue.put_nowait(_SENTINEL)
        await self._drain_task
        self._drain_task = None

    # ── record / drain ───────────────────────────────────────────────

    def _emit(self, record: dict):
        """Non-blocking: queue and return immediately."""
        self._queue.put_nowait(record)

    async def _drain(self):
        """Background task: drain queue and append to JSONL."""
        with open(self.path, "a", encoding="utf-8") as f:
            while True:
                record = await self._queue.get()
                if record is _SENTINEL:
                    self._queue.task_done()
                    break
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                self._queue.task_done()

    def record(self, layer: str, direction: str, data: dict, run_id: str = ""):
        record = {
            "ts": time.time(),
            "layer": layer,
            "direction": direction,
            "run_id": run_id,
            "data": data,
        }
        self._emit(record)

    # ── convenience helpers ──────────────────────────────────────────

    def on_ipc_request(self, action: str, payload: dict, run_id: str = ""):
        self.record("ipc", "client->core", {"action": action, "payload": payload}, run_id=run_id)

    def on_ipc_response(self, action: str, result: dict, run_id: str = ""):
        self.record("ipc", "core->client", {"action": action, "result": result}, run_id=run_id)

    async def on_eventbus_event(self, event):
        self.record("event", "core", {
            "event_type": event.event_type.value,
            "topic": event.get_topic(),
            "data": event.data,
        }, run_id=event.run_id)

    def on_llm_request(self, model: str, message_count: int, tool_count: int, run_id: str = ""):
        self.record("llm", "core->llm", {
            "model": model,
            "message_count": message_count,
            "tool_count": tool_count,
        }, run_id=run_id)

    def on_llm_response(self, model: str, content_preview: str, tool_calls: int,
                        tokens_used: int, latency_ms: float, run_id: str = ""):
        self.record("llm", "llm->core", {
            "model": model,
            "content_preview": content_preview,
            "tool_calls": tool_calls,
            "tokens_used": tokens_used,
            "latency_ms": latency_ms,
        }, run_id=run_id)

    def on_llm_error(self, model: str, error: str, latency_ms: float, run_id: str = ""):
        self.record("llm", "llm->core", {
            "model": model,
            "error": error,
            "latency_ms": latency_ms,
        }, run_id=run_id)

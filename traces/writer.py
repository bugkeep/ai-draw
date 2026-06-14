import os
import json
import asyncio


class TraceWriter:
    """Async JSONL file writer with a queue-drain pattern.

    ``emit()`` enqueues a dict in O(1) and returns immediately — the main
    event loop never waits for file I/O.  A background ``_drain`` task
    continuously dequeues records and appends them as JSON lines.

    On shutdown, ``stop()`` first awaits ``queue.join()`` — all records
    that were already emitted are guaranteed to be written before the
    drain task is cancelled.  ``task_done()`` is called in a ``finally``
    block so ``join()`` never stalls, even if ``f.write()`` throws.
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._drain_task: asyncio.Task | None = None

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self):
        if self._drain_task is not None:
            return
        self._drain_task = asyncio.create_task(self._drain())

    async def stop(self):
        if self._drain_task is None:
            return
        # Wait for all already-emitted records to be written to disk.
        await self._queue.join()
        # Drain task is now blocked on get() waiting for new items —
        # cancel it so the event loop can exit.
        self._drain_task.cancel()
        try:
            await self._drain_task
        except asyncio.CancelledError:
            pass
        self._drain_task = None

    # ── emit / drain ─────────────────────────────────────────────────

    def emit(self, record: dict):
        """Enqueue a record.  Non-blocking, always returns immediately."""
        self._queue.put_nowait(record)

    async def _drain(self):
        with open(self.path, "a", encoding="utf-8") as f:
            while True:
                try:
                    record = await self._queue.get()
                except asyncio.CancelledError:
                    # Cancelled while waiting for an item — no item was
                    # dequeued so no task_done() is needed.
                    raise
                try:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
                finally:
                    self._queue.task_done()

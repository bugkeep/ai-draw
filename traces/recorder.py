import os
import json
import time


TRACES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "traces"))


class DaemonTracer:
    """System-wide timeline recorder.

    Appends structured records to traces/daemon.jsonl covering five
    directions that span the daemon's process boundary:

      layer    direction       description
      ─────    ─────────       ──────────────────────────
      ipc      client->core    JSON-RPC command received
      ipc      core->client    response / push event sent
      event    core            internal EventBus dispatch
      llm      core->llm       LLM API request sent
      llm      llm->core       LLM API response received
    """

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(TRACES_DIR, "daemon.jsonl")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def record(self, layer: str, direction: str, data: dict, run_id: str = ""):
        record = {
            "ts": time.time(),
            "layer": layer,
            "direction": direction,
            "run_id": run_id,
            "data": data,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ── convenience helpers ──────────────────────────────────────────

    def on_ipc_request(self, action: str, payload: dict, run_id: str = ""):
        self.record("ipc", "client->core", {"action": action, "payload": payload}, run_id=run_id)

    def on_ipc_response(self, action: str, result: dict, run_id: str = ""):
        self.record("ipc", "core->client", {"action": action, "result": result}, run_id=run_id)

    async def on_eventbus_event(self, event):
        """Register as EventBus global handler to trace all internal events."""
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

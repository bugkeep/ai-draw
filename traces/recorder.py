import os
import time
from .writer import TraceWriter


TRACES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "traces"))
SENSITIVE_KEYS = {"api_key", "authorization", "password", "secret", "token"}


def redact_secrets(value):
    """Return trace-safe data without mutating the original payload."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    return value


class DaemonTracer:
    """Build trace records and hand them to a background ``TraceWriter``.

    Each trace record is a dict with fields:

      ts         float   unix timestamp
      layer      str     subsystem: ipc | event | llm
      direction  str     data flow: client→core | core→client | core | core→llm | llm→core
      kind       str     finer classification within direction
      run_id     str     optional, empty if not applicable
      step       int     agent loop round number (0 if not applicable)
      client_id  str     TCP client that originated the request
      data       dict    payload — schema varies per trace point

    layer + direction  →  meaning
    ─────────────────────────────────────────────────────
    ipc   client→core    JSON-RPC command received         kind="request"
    ipc   core→client    response to a command             kind="response"
    event core           internal EventBus dispatch        kind=<topic>
    llm   core→llm       LLM API request                   kind="request"
    llm   llm→core       LLM API response or error         kind="response"|"error"
    """

    def __init__(self, path: str | None = None):
        path = path or os.path.join(TRACES_DIR, "daemon.jsonl")
        self._writer = TraceWriter(path)

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self):
        self._writer.start()

    async def stop(self):
        await self._writer.stop()

    # ── core record builder ──────────────────────────────────────────

    def _emit(self, record: dict):
        self._writer.emit(record)

    def record(self, layer: str, direction: str, kind: str = "",
               run_id: str = "", step: int = 0, client_id: str = "",
               data: dict | None = None):
        self._emit({
            "ts": time.time(),
            "layer": layer,
            "direction": direction,
            "kind": kind,
            "run_id": run_id,
            "step": step,
            "client_id": client_id,
            "data": redact_secrets(data or {}),
        })

    # ── IPC helpers ──────────────────────────────────────────────────

    def on_ipc_request(self, action: str, payload: dict, *,
                       run_id: str = "", client_id: str = ""):
        self.record("ipc", "client->core", kind="request",
                    run_id=run_id, client_id=client_id,
                    data={"action": action, "payload": payload})

    def on_ipc_response(self, action: str, result: dict, *,
                        run_id: str = "", client_id: str = ""):
        self.record("ipc", "core->client", kind="response",
                    run_id=run_id, client_id=client_id,
                    data={"action": action, "result": result})

    def on_ipc_push(self, sub_id: str, event_type: str, *,
                    run_id: str = ""):
        """Push trace — lightweight, no event body (already in event layer)."""
        self.record("ipc", "core->client", kind="push",
                    run_id=run_id,
                    data={"sub_id": sub_id, "event_type": event_type})

    # ── EventBus helper ──────────────────────────────────────────────

    async def on_eventbus_event(self, event):
        step = event.data.get("round", 0) if event.data else 0
        self.record("event", "core", kind=event.get_topic(),
                    run_id=event.run_id, step=step,
                    data={
                        "event_type": event.event_type.value,
                        "topic": event.get_topic(),
                        "payload": event.data,
                    })

    # ── LLM helpers ──────────────────────────────────────────────────

    def on_llm_request(self, model: str, message_count: int,
                       tool_count: int, *, run_id: str = "", step: int = 0,
                       messages: list | None = None):
        data: dict = {
            "model": model,
            "message_count": message_count,
            "tool_count": tool_count,
        }
        if messages is not None:
            data["messages"] = messages
        self.record("llm", "core->llm", kind="request",
                    run_id=run_id, step=step, data=data)

    def on_llm_response(self, model: str, content_preview: str,
                        tool_calls: int, tokens_used: int,
                        latency_ms: float, *, run_id: str = "", step: int = 0,
                        content: str | None = None):
        data: dict = {
            "model": model,
            "content_preview": content_preview,
            "tool_calls": tool_calls,
            "tokens_used": tokens_used,
            "latency_ms": latency_ms,
        }
        if content is not None:
            data["content"] = content
        self.record("llm", "llm->core", kind="response",
                    run_id=run_id, step=step, data=data)

    def on_llm_error(self, model: str, error: str, latency_ms: float, *,
                     run_id: str = "", step: int = 0):
        self.record("llm", "llm->core", kind="error",
                    run_id=run_id, step=step,
                    data={
                        "model": model,
                        "error": error,
                        "latency_ms": latency_ms,
                    })

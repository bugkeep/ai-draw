import time
from traces import DaemonTracer
from .base import LLMProvider, LLMResponse


class TracingProvider:
    """LLM provider decorator that records llm-layer trace records.

    Wraps an inner ``LLMProvider`` and records ``core->llm`` (request)
    and ``llm->core`` (response/error) trace records via ``DaemonTracer``,
    keeping the inner provider free of tracing concerns.

    ``include_payload`` controls whether the full ``messages`` array and
    full response ``content`` are included in the trace data (default on).
    Disable in production if prompts contain sensitive information or
    trace file size needs to be constrained.
    """

    def __init__(self, inner: LLMProvider, tracer: DaemonTracer,
                 include_payload: bool = True):
        self._inner = inner
        self._tracer = tracer
        self.include_payload = include_payload

    @property
    def model(self) -> str:
        return getattr(self._inner, "default_model",
                       getattr(self._inner, "model", ""))

    async def achat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        *,
        step: int = 0,
    ) -> LLMResponse:
        model_name = model or self.model
        tool_count = len(tools) if tools else 0

        self._tracer.on_llm_request(
            model_name, len(messages), tool_count,
            step=step,
            messages=messages if self.include_payload else None,
        )

        t0 = time.time()
        try:
            response = await self._inner.achat(messages, tools, model)
        except BaseException:
            latency_ms = round((time.time() - t0) * 1000, 1)
            self._tracer.on_llm_error(model_name, "", latency_ms, step=step)
            raise

        latency_ms = round((time.time() - t0) * 1000, 1)
        self._tracer.on_llm_response(
            model_name, (response.content or "")[:200],
            len(response.tool_calls), response.tokens_used, latency_ms,
            step=step,
            content=response.content if self.include_payload else None,
        )
        return response

    async def chat(self, messages, tools=None, model=None):
        """Sync fallback — delegates to inner without tracing."""
        return await self._inner.chat(messages, tools, model)

import asyncio
import copy
import hashlib
import json
import math
import os
import random
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from weakref import WeakKeyDictionary

import httpx

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from litellm.utils import supports_httpx_timeout
from openai import AsyncOpenAI, OpenAIError, RateLimitError

from .settings import ModelConfig, PROVIDER_BY_ID


litellm.suppress_debug_info = True
litellm.turn_off_message_logging = True


class ModelGatewayError(RuntimeError):
    pass


def rate_limit_delay(retry_after: str | None, attempt: int) -> float:
    delay = random.uniform(1, 2) * 2 ** min(attempt, 6)
    if retry_after:
        try:
            server_delay = float(retry_after)
        except ValueError:
            try:
                date = parsedate_to_datetime(retry_after)
                server_delay = (date - datetime.now(date.tzinfo or timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                server_delay = 0
        if math.isfinite(server_delay):
            delay = max(delay, server_delay)
    return delay


def request_options(config: ModelConfig) -> dict[str, Any]:
    route = PROVIDER_BY_ID[config.provider]["route"]
    prefix = f"{route}/" if route else ""
    model = config.model
    if prefix and model.startswith(prefix):
        model = model[len(prefix):]
    if config.api_mode == "responses" and not model.startswith("responses/"):
        model = f"responses/{model}"
    provider = route or model.split("/", 1)[0]
    timeout = httpx.Timeout(None) if supports_httpx_timeout(provider) else float("inf")
    options: dict[str, Any] = {"model": f"{prefix}{model}", "timeout": timeout,
                               "num_retries": 0, "max_retries": 0, "stream": False}
    if config.api_base:
        options["api_base"] = config.api_base
    if config.api_version:
        options["api_version"] = config.api_version
    if config.api_key.get_secret_value():
        options["api_key"] = config.api_key.get_secret_value()
    parameters = copy.deepcopy(config.request_parameters)
    if parameters:
        if route in {"openai", "hosted_vllm"}:
            options["extra_body"] = parameters
        else:
            options.update(parameters)
    return options


class ProviderConnection:
    def __init__(self, config: ModelConfig):
        self.config = config.model_copy(deep=True)
        self.queue = asyncio.Lock()
        self.blocked_until = 0.0
        self.last_start = 0.0
        self.interval = 0.0
        self.successes = 0
        self.client: AsyncOpenAI | None = None

    async def acquire(self, on_state: Callable[[dict], None] | None = None) -> None:
        async with self.queue:
            while True:
                delay = max(self.blocked_until, self.last_start + self.interval) - time.monotonic()
                if delay <= 0:
                    self.last_start = time.monotonic()
                    return
                if on_state:
                    on_state({"phase": "model_wait", "wait_seconds": round(delay, 2),
                              "retry_at": time.time() + delay})
                await asyncio.sleep(delay)

    def rate_limited(self, delay: float) -> None:
        self.blocked_until = max(self.blocked_until, time.monotonic() + delay)
        self.interval = max(self.interval, min(delay, 60.0))
        self.successes = 0

    def succeeded(self) -> None:
        self.successes += 1
        self.interval *= 0.7
        if self.successes >= 4:
            self.interval = 0.0

    async def complete(self, config: ModelConfig, options: dict):
        route = PROVIDER_BY_ID[config.provider]["route"]
        if route in {"openai", "hosted_vllm"} and config.api_mode == "chat_completions":
            if self.client is None:
                self.client = AsyncOpenAI(api_key=self.config.api_key.get_secret_value() or "local",
                                          base_url=self.config.api_base or None,
                                          timeout=None, max_retries=0)
            model = config.model.removeprefix(f"{route}/")
            return await self.client.chat.completions.create(
                model=model, messages=options["messages"], stream=False,
                **({"tools": options["tools"], "tool_choice": "auto"} if options.get("tools") else {}),
                **({"extra_body": copy.deepcopy(config.request_parameters)}
                   if config.request_parameters else {}),
            )
        return await litellm.acompletion(**options)


_connections: WeakKeyDictionary = WeakKeyDictionary()


def model_connection(config: ModelConfig) -> ProviderConnection:
    identity = json.dumps([PROVIDER_BY_ID[config.provider]["route"], config.api_base,
                           config.api_key.get_secret_value(), config.api_version, config.api_mode],
                          ensure_ascii=False).encode()
    key = hashlib.sha256(identity).hexdigest()
    connections = _connections.setdefault(asyncio.get_running_loop(), {})
    if key not in connections:
        connections[key] = ProviderConnection(config)
    return connections[key]


class ModelGateway:
    def __init__(self, config: ModelConfig):
        if not config.configured:
            raise ValueError("请在设置页面完成模型配置。")
        self.config = config.model_copy(deep=True)

    def error_message(self, error: OpenAIError) -> str:
        message = str(error)
        key = self.config.api_key.get_secret_value()
        return message.replace(key, "[redacted]")[:1500] if key else message[:1500]

    async def complete(self, messages: list[dict], tools: list[dict] | None = None,
                       on_retry: Callable[[dict], None] | None = None,
                       on_state: Callable[[dict], None] | None = None):
        options = request_options(self.config)
        options["messages"] = copy.deepcopy(messages)
        if tools:
            options.update(tools=tools, tool_choice="auto")
        attempt = 0
        connection = model_connection(self.config)
        while True:
            await connection.acquire(on_state)
            try:
                if on_state:
                    on_state({"phase": "model_request", "attempt": attempt + 1})
                response = await connection.complete(self.config, options)
                connection.succeeded()
                break
            except RateLimitError as exc:
                message = self.error_message(exc)
                if getattr(exc, "rate_limit_type", None) == "budget" or any(
                    code in message.lower() for code in ("insufficient_quota", "quota_exhausted", "billing_hard_limit")
                ):
                    raise ModelGatewayError(message) from None
                headers = exc.response.headers if exc.response is not None else {}
                delay = rate_limit_delay(headers.get("retry-after"), attempt)
                connection.rate_limited(delay)
                attempt += 1
                if on_retry:
                    on_retry({"attempt": attempt, "wait_seconds": round(delay, 2),
                              "retry_at": time.time() + delay, "message": message})
            except OpenAIError as exc:
                raise ModelGatewayError(self.error_message(exc)) from None
        if not response.choices:
            raise ModelGatewayError("模型没有返回可用的响应。")
        return response


async def test_connection(config: ModelConfig) -> dict[str, Any]:
    started = time.perf_counter()
    gateway = ModelGateway(config)
    tools = [{"type": "function", "function": {
        "name": "check_drawing_connection", "description": "Check the drawing application's model connection.",
        "parameters": {"type": "object", "properties": {"value": {"type": "string", "enum": ["draw-agent"]}},
                       "required": ["value"], "additionalProperties": False},
    }}]
    messages = [{"role": "system", "content": "Call check_drawing_connection with value draw-agent. After receiving the tool result, reply with the text connected."},
                {"role": "user", "content": "Check the drawing tool connection."}]
    response = await gateway.complete(messages, tools)
    message = response.choices[0].message
    if not message.tool_calls:
        raise ModelGatewayError("模型返回了文本，但未完成工具调用。请确认该模型支持 function calling。")
    messages.append(message.model_dump(exclude_none=True))
    for call in message.tool_calls:
        if call.function.name != "check_drawing_connection" or json.loads(call.function.arguments) != {"value": "draw-agent"}:
            raise ModelGatewayError("模型返回的工具参数未通过校验。")
        messages.append({"role": "tool", "tool_call_id": call.id, "content": '{"connected":true}'})
    final = await gateway.complete(messages)
    if not final.choices[0].message.content:
        raise ModelGatewayError("工具调用完成后，模型未返回文本响应。")
    return {"ok": True, "text_response": True, "tool_calling": True,
            "model": config.model, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}


async def close_model_clients() -> None:
    connections = _connections.pop(asyncio.get_running_loop(), {})
    for connection in connections.values():
        if connection.client is not None:
            await connection.client.close()
    await litellm.close_litellm_async_clients()
    litellm.in_memory_llm_clients_cache.flush_cache()

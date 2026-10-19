import copy
import json
import os
import time
from typing import Any

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from openai import OpenAIError

from .settings import ModelConfig, PROVIDER_BY_ID


litellm.suppress_debug_info = True
litellm.turn_off_message_logging = True


class ModelGatewayError(RuntimeError):
    pass


def request_options(config: ModelConfig) -> dict[str, Any]:
    route = PROVIDER_BY_ID[config.provider]["route"]
    prefix = f"{route}/" if route else ""
    model = config.model
    if prefix and model.startswith(prefix):
        model = model[len(prefix):]
    if config.api_mode == "responses" and not model.startswith("responses/"):
        model = f"responses/{model}"
    options: dict[str, Any] = {"model": f"{prefix}{model}", "timeout": config.timeout_seconds,
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


class ModelGateway:
    def __init__(self, config: ModelConfig):
        if not config.configured:
            raise ValueError("请在设置页面完成模型配置。")
        self.config = config.model_copy(deep=True)

    async def complete(self, messages: list[dict], tools: list[dict] | None = None):
        options = request_options(self.config)
        options["messages"] = copy.deepcopy(messages)
        if tools:
            options.update(tools=tools, tool_choice="auto")
        try:
            response = await litellm.acompletion(**options)
        except OpenAIError as exc:
            message = str(exc)
            key = self.config.api_key.get_secret_value()
            if key:
                message = message.replace(key, "[redacted]")
            raise ModelGatewayError(message[:1500]) from None
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
    await litellm.close_litellm_async_clients()
    litellm.in_memory_llm_clients_cache.flush_cache()

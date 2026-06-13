import os
from openai import OpenAI, AsyncOpenAI
from providers.base import LLMProvider, LLMResponse, ToolCall


class BailianProvider:
    BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen-plus",
        enable_prompt_caching: bool = False,
    ):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.default_model = model
        self.enable_prompt_caching = enable_prompt_caching
        self._client = None
        self._async_client = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.BASE_URL,
            )
        return self._client

    def _get_async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.BASE_URL,
            )
        return self._async_client

    def _inject_cache_control(self, messages: list[dict]) -> list[dict]:
        """Mark system messages for prompt caching.

        Creates a deep copy so the original messages are not mutated.
        """
        if not self.enable_prompt_caching:
            return messages
        import copy
        cached = copy.deepcopy(messages)
        for msg in cached:
            if msg.get("role") == "system":
                msg["cache_control"] = {"type": "ephemeral"}
        return cached

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        kwargs: dict = {
            "model": model or self.default_model,
            "messages": self._inject_cache_control(messages),
        }
        if tools:
            kwargs["tools"] = tools

        response = client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0

        tool_calls = []
        if response.choices[0].message.tool_calls:
            for tc in response.choices[0].message.tool_calls:
                tool_calls.append(ToolCall.from_openai(tc.model_dump()))

        return LLMResponse(
            content=content,
            model=response.model,
            tokens_used=token_usage,
            tool_calls=tool_calls,
            raw=response,
        )

    async def achat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        *,
        step: int = 0,
    ) -> LLMResponse:
        client = self._get_async_client()
        kwargs: dict = {
            "model": model or self.default_model,
            "messages": self._inject_cache_control(messages),
        }
        if tools:
            kwargs["tools"] = tools

        response = await client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0

        tool_calls = []
        if response.choices[0].message.tool_calls:
            for tc in response.choices[0].message.tool_calls:
                tool_calls.append(ToolCall.from_openai(tc.model_dump()))

        return LLMResponse(
            content=content,
            model=response.model,
            tokens_used=token_usage,
            tool_calls=tool_calls,
            raw=response,
        )

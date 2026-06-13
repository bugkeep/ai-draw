import os
from openai import OpenAI, AsyncOpenAI
from providers.base import LLMProvider, LLMResponse, ToolCall


class OpenAIProvider:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o",
        base_url: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.default_model = model
        self.base_url = base_url
        self._client = None
        self._async_client = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _get_async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._async_client = AsyncOpenAI(**kwargs)
        return self._async_client

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        kwargs: dict = {
            "model": model or self.default_model,
            "messages": messages,
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
    ) -> LLMResponse:
        client = self._get_async_client()
        kwargs: dict = {
            "model": model or self.default_model,
            "messages": messages,
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

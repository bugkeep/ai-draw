from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError

from providers.bailian_provider import BailianProvider
from providers.base import LLMResponse


@pytest.mark.asyncio
async def test_async_connection_error_uses_curl_fallback():
    provider = BailianProvider(api_key="test-key")
    create = AsyncMock(side_effect=APIConnectionError(request=httpx.Request("POST", "https://example.com")))
    provider._async_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    provider._curl_achat = AsyncMock(return_value={
        "model": "qwen-plus",
        "usage": {"total_tokens": 12},
        "choices": [{"message": {"content": "ok"}}],
    })

    response = await provider.achat([{"role": "user", "content": "hello"}])

    assert isinstance(response, LLMResponse)
    assert response.content == "ok"
    assert response.tokens_used == 12
    provider._curl_achat.assert_awaited_once()


def test_curl_mapping_parses_tool_calls():
    response = BailianProvider._response_from_mapping({
        "model": "qwen-plus",
        "usage": {"total_tokens": 24},
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "draw_rect",
                        "arguments": '{"width": 20, "height": 30}',
                    },
                }],
            },
        }],
    })

    assert response.tool_calls[0].name == "draw_rect"
    assert response.tool_calls[0].arguments == {"width": 20, "height": 30}

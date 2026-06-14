import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from providers.openai_provider import OpenAIProvider
from providers.base import LLMResponse, ToolCall


def mock_openai_response(content="Hello", tool_calls=None, model="gpt-4o", tokens=100):
    response = MagicMock()
    response.model = model
    response.usage.total_tokens = tokens
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = tool_calls
    response.choices = [choice]
    return response


def mock_tool_call(id="call_1", name="draw_circle", arguments='{"x": 10}'):
    tc = MagicMock()
    tc.id = id
    tc.function.name = name
    tc.function.arguments = arguments
    tc.model_dump.return_value = {
        "id": id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    return tc


class TestOpenAIProviderInit:
    def test_default_model(self):
        provider = OpenAIProvider(api_key="test-key")
        assert provider.default_model == "gpt-4o"

    def test_custom_model(self):
        provider = OpenAIProvider(api_key="test-key", model="gpt-3.5-turbo")
        assert provider.default_model == "gpt-3.5-turbo"

    def test_custom_base_url(self):
        provider = OpenAIProvider(api_key="test-key", base_url="http://localhost:8080")
        assert provider.base_url == "http://localhost:8080"


class TestOpenAIProviderChat:
    @patch("providers.openai_provider.OpenAI")
    def test_chat_no_tools(self, MockOpenAI):
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_openai_response(content="Hi there")

        provider = OpenAIProvider(api_key="test-key")
        result = provider.chat(messages=[{"role": "user", "content": "hello"}])

        assert isinstance(result, LLMResponse)
        assert result.content == "Hi there"
        assert result.model == "gpt-4o"
        assert result.tokens_used == 100
        assert result.tool_calls == []

    @patch("providers.openai_provider.OpenAI")
    def test_chat_with_tools(self, MockOpenAI):
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client

        tc = mock_tool_call(id="call_1", name="draw_circle", arguments='{"x": 10, "y": 20}')
        mock_client.chat.completions.create.return_value = mock_openai_response(
            content="Drawing", tool_calls=[tc]
        )

        provider = OpenAIProvider(api_key="test-key")
        tools = [{"type": "function", "function": {"name": "draw_circle"}}]
        result = provider.chat(
            messages=[{"role": "user", "content": "draw"}],
            tools=tools,
        )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "draw_circle"
        assert result.tool_calls[0].arguments == {"x": 10, "y": 20}

    @patch("providers.openai_provider.OpenAI")
    def test_chat_custom_model(self, MockOpenAI):
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_openai_response()

        provider = OpenAIProvider(api_key="test-key", model="gpt-4")
        provider.chat(messages=[], model="gpt-3.5-turbo")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-3.5-turbo"

    @patch("providers.openai_provider.OpenAI")
    def test_chat_no_content(self, MockOpenAI):
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        resp = mock_openai_response(content=None)
        resp.choices[0].message.content = None
        mock_client.chat.completions.create.return_value = resp

        provider = OpenAIProvider(api_key="test-key")
        result = provider.chat(messages=[])
        assert result.content == ""


class TestOpenAIProviderAsyncChat:
    @pytest.mark.asyncio
    @patch("providers.openai_provider.AsyncOpenAI")
    async def test_achat_no_tools(self, MockAsyncOpenAI):
        mock_client = AsyncMock()
        MockAsyncOpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_openai_response(content="Async hello")

        provider = OpenAIProvider(api_key="test-key")
        result = await provider.achat(messages=[{"role": "user", "content": "hello"}])

        assert result.content == "Async hello"
        assert result.tool_calls == []

    @pytest.mark.asyncio
    @patch("providers.openai_provider.AsyncOpenAI")
    async def test_achat_with_tools(self, MockAsyncOpenAI):
        mock_client = AsyncMock()
        MockAsyncOpenAI.return_value = mock_client

        tc = mock_tool_call(id="call_2", name="draw_rect", arguments='{"w": 100}')
        mock_client.chat.completions.create.return_value = mock_openai_response(
            content="Drawing rect", tool_calls=[tc]
        )

        provider = OpenAIProvider(api_key="test-key")
        result = await provider.achat(
            messages=[{"role": "user", "content": "draw rect"}],
            tools=[{"type": "function"}],
        )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "draw_rect"

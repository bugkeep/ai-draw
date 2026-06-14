import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from openai import APIConnectionError, OpenAI, AsyncOpenAI
from providers.base import LLMProvider, LLMResponse, ToolCall, get_context_window


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
        self.base_url = os.environ.get("BAILIAN_BASE_URL", self.BASE_URL)
        self._client = None
        self._async_client = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=0,
            )
        return self._client

    def _get_async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=0,
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

        try:
            response = client.chat.completions.create(**kwargs)
        except APIConnectionError:
            return self._response_from_mapping(self._curl_chat(kwargs))

        content = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0
        model_name = response.model
        cw = get_context_window(model_name)

        tool_calls = []
        if response.choices[0].message.tool_calls:
            for tc in response.choices[0].message.tool_calls:
                tool_calls.append(ToolCall.from_openai(tc.model_dump()))

        return LLMResponse(
            content=content,
            model=model_name,
            tokens_used=token_usage,
            context_window=cw,
            context_pct=round(token_usage / cw, 4) if cw else 0.0,
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

        try:
            response = await client.chat.completions.create(**kwargs)
        except APIConnectionError:
            return self._response_from_mapping(await self._curl_achat(kwargs))

        content = response.choices[0].message.content or ""
        token_usage = response.usage.total_tokens if response.usage else 0
        model_name = response.model
        cw = get_context_window(model_name)

        tool_calls = []
        if response.choices[0].message.tool_calls:
            for tc in response.choices[0].message.tool_calls:
                tool_calls.append(ToolCall.from_openai(tc.model_dump()))

        return LLMResponse(
            content=content,
            model=model_name,
            tokens_used=token_usage,
            context_window=cw,
            context_pct=round(token_usage / cw, 4) if cw else 0.0,
            tool_calls=tool_calls,
            raw=response,
        )

    def _curl_command(self, body_path: Path, headers_path: Path) -> list[str]:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        enabled = os.environ.get("BAILIAN_CURL_FALLBACK", "1").lower()
        if not curl or enabled in {"0", "false", "no"}:
            raise RuntimeError("Bailian connection failed and curl fallback is unavailable")
        return [
            curl,
            "--silent",
            "--show-error",
            "--connect-timeout",
            "10",
            "--max-time",
            os.environ.get("BAILIAN_CURL_TIMEOUT", "180"),
            "--header",
            f"@{headers_path}",
            "--data-binary",
            f"@{body_path}",
            f"{self.base_url.rstrip('/')}/chat/completions",
        ]

    def _write_curl_files(self, directory: str, kwargs: dict) -> tuple[Path, Path]:
        body_path = Path(directory) / "request.json"
        headers_path = Path(directory) / "headers.txt"
        body_path.write_text(json.dumps(kwargs, ensure_ascii=False), encoding="utf-8")
        headers_path.write_text(
            f"Authorization: Bearer {self.api_key}\nContent-Type: application/json\n",
            encoding="utf-8",
        )
        os.chmod(headers_path, 0o600)
        return body_path, headers_path

    def _curl_chat(self, kwargs: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="bailian-") as directory:
            body_path, headers_path = self._write_curl_files(directory, kwargs)
            result = subprocess.run(
                self._curl_command(body_path, headers_path),
                capture_output=True,
                check=False,
            )
            return self._parse_curl_result(result.returncode, result.stdout, result.stderr)

    async def _curl_achat(self, kwargs: dict) -> dict:
        return await asyncio.to_thread(self._curl_chat, kwargs)

    @staticmethod
    def _parse_curl_result(returncode: int, stdout: bytes, stderr: bytes) -> dict:
        if returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:300]
            raise RuntimeError(f"Bailian curl fallback failed: {detail}")
        try:
            data = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Bailian curl fallback returned invalid JSON") from exc
        if error := data.get("error"):
            code = error.get("code", "api_error")
            message = error.get("message", "Bailian request failed")
            raise RuntimeError(f"{code}: {message}")
        return data

    @staticmethod
    def _response_from_mapping(response: dict) -> LLMResponse:
        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = response.get("usage") or {}
        model_name = response.get("model", "")
        token_usage = usage.get("total_tokens", 0)
        tool_calls = [
            ToolCall.from_openai(tool_call)
            for tool_call in message.get("tool_calls") or []
        ]
        context_window = get_context_window(model_name)
        return LLMResponse(
            content=message.get("content") or "",
            model=model_name,
            tokens_used=token_usage,
            context_window=context_window,
            context_pct=round(token_usage / context_window, 4) if context_window else 0.0,
            tool_calls=tool_calls,
            raw=response,
        )

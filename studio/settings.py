import json
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .store import StudioStore


PROVIDERS = [
    {"id": "openai", "name": "OpenAI", "route": "openai", "api_base": "https://api.openai.com/v1"},
    {"id": "anthropic", "name": "Anthropic Claude", "route": "anthropic", "api_base": ""},
    {"id": "gemini", "name": "Google Gemini", "route": "gemini", "api_base": ""},
    {"id": "azure", "name": "Azure OpenAI", "route": "azure", "api_base": "", "requires_base": True},
    {"id": "ark", "name": "火山方舟 / Ark", "route": "openai", "api_base": "https://ark.cn-beijing.volces.com/api/plan/v3"},
    {"id": "deepseek", "name": "DeepSeek", "route": "deepseek", "api_base": ""},
    {"id": "dashscope", "name": "阿里百炼 / 通义千问", "route": "dashscope", "api_base": ""},
    {"id": "moonshot", "name": "Moonshot / Kimi", "route": "moonshot", "api_base": ""},
    {"id": "zai", "name": "智谱 GLM / Z.ai", "route": "zai", "api_base": ""},
    {"id": "minimax", "name": "MiniMax", "route": "minimax", "api_base": ""},
    {"id": "siliconflow", "name": "硅基流动 / SiliconFlow", "route": "openai", "api_base": "https://api.siliconflow.cn/v1"},
    {"id": "xai", "name": "xAI Grok", "route": "xai", "api_base": ""},
    {"id": "groq", "name": "Groq", "route": "groq", "api_base": ""},
    {"id": "mistral", "name": "Mistral", "route": "mistral", "api_base": ""},
    {"id": "openrouter", "name": "OpenRouter", "route": "openrouter", "api_base": ""},
    {"id": "together_ai", "name": "Together AI", "route": "together_ai", "api_base": ""},
    {"id": "cohere_chat", "name": "Cohere", "route": "cohere_chat", "api_base": ""},
    {"id": "bedrock", "name": "Amazon Bedrock", "route": "bedrock", "api_base": "", "optional_key": True},
    {"id": "vertex_ai", "name": "Google Vertex AI", "route": "vertex_ai", "api_base": "", "optional_key": True},
    {"id": "ollama", "name": "Ollama", "route": "ollama_chat", "api_base": "http://localhost:11434", "optional_key": True},
    {"id": "local", "name": "本地 OpenAI 兼容服务", "route": "hosted_vllm", "api_base": "http://localhost:1234/v1", "optional_key": True},
    {"id": "openai_compatible", "name": "自定义 OpenAI 兼容接口", "route": "openai", "api_base": "", "requires_base": True},
    {"id": "litellm", "name": "其他厂商 / LiteLLM", "route": "", "api_base": "", "optional_key": True},
]
PROVIDER_BY_ID = {item["id"]: item for item in PROVIDERS}
RESERVED_PARAMETERS = {
    "model", "messages", "tools", "tool_choice", "stream", "api_base", "base_url", "api_key", "api_version",
    "client", "timeout", "max_retries", "num_retries", "fallbacks", "mock_response", "custom_llm_provider",
    "success_callback", "failure_callback", "callbacks", "logger_fn", "litellm_logging_obj",
    "extra_headers", "headers", "authorization", "password", "extra_body",
}


def check_parameters(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = key.lower()
            if lowered in RESERVED_PARAMETERS or any(word in lowered for word in ("secret", "credential", "api_key", "access_key", "token")) and lowered not in {"max_tokens", "max_completion_tokens", "thinking_budget_tokens"}:
                raise ValueError(f"扩展参数不可包含连接凭据或受保护字段：{key}")
            check_parameters(item)
    elif isinstance(value, list):
        for item in value:
            check_parameters(item)


class ModelForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "ark"
    model: str = Field(default="", max_length=200)
    api_base: str = Field(default="", max_length=1000)
    api_version: str = Field(default="", max_length=80)
    api_mode: Literal["chat_completions", "responses"] = "chat_completions"
    request_parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, value):
        if isinstance(value, dict):
            value = {key: item for key, item in value.items() if key != "timeout_seconds"}
        return value

    @model_validator(mode="after")
    def validate_connection(self):
        if self.provider not in PROVIDER_BY_ID:
            raise ValueError("请选择支持的厂商或自定义接口。")
        if self.api_mode == "responses" and PROVIDER_BY_ID[self.provider]["route"] not in {"openai", "azure"}:
            raise ValueError("当前厂商请使用 Chat Completions 适配模式。")
        self.model = self.model.strip()
        self.api_base = self.api_base.strip().rstrip("/")
        if self.api_base:
            parts = urlsplit(self.api_base)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError("API 地址应为不含凭据、查询参数和片段的 HTTP 或 HTTPS Base URL。")
            if parts.path.endswith(("/chat/completions", "/responses", "/messages")):
                raise ValueError("请填写 Base URL，不要附加 chat/completions、responses 或 messages。")
        if len(json.dumps(self.request_parameters, ensure_ascii=False)) > 10000:
            raise ValueError("扩展参数过长。")
        check_parameters(self.request_parameters)
        return self


class ModelConfig(ModelForm):
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))

    @property
    def configured(self) -> bool:
        preset = PROVIDER_BY_ID[self.provider]
        return bool(self.model and (self.api_base or not preset.get("requires_base"))
                    and (self.api_key.get_secret_value() or preset.get("optional_key")))


class SettingsUpdate(ModelForm):
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    revision: int = Field(default=0, ge=0)


class ModelSettings:
    def __init__(self, store: StudioStore):
        self.store = store
        self.key_path = Path(os.getenv("DRAW_AGENT_SETTINGS_KEY_FILE", str(store.database_path.parent / "model-settings.key")))

    def _cipher(self, create: bool = False) -> Fernet:
        if not self.key_path.exists():
            if not create:
                raise RuntimeError("模型设置加密文件缺失，请恢复 model-settings.key 后重试。")
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(Fernet.generate_key())
        return Fernet(self.key_path.read_bytes())

    def load(self) -> ModelConfig:
        row = self.store.get_model_settings()
        if row:
            key = self._cipher().decrypt(row["encrypted_key"].encode()).decode() if row["encrypted_key"] else ""
            return ModelConfig(**row["config"], api_key=key)
        key = os.getenv("DRAW_AGENT_API_KEY") or os.getenv("ARK_API_KEY", "")
        provider = os.getenv("DRAW_AGENT_PROVIDER", "ark")
        default_base = PROVIDER_BY_ID.get(provider, {}).get("api_base", "")
        parameters = json.loads(os.getenv("DRAW_AGENT_REQUEST_PARAMETERS", "{}"))
        if provider == "ark" and not parameters:
            parameters = {"thinking": {"type": os.getenv("DRAW_AGENT_THINKING", "disabled")}}
        return ModelConfig(
            provider=provider, model=os.getenv("DRAW_AGENT_MODEL", "ark-code-latest" if provider == "ark" else ""),
            api_base=os.getenv("DRAW_AGENT_BASE_URL", default_base), api_key=key,
            api_version=os.getenv("DRAW_AGENT_API_VERSION", ""), request_parameters=parameters,
            api_mode=os.getenv("DRAW_AGENT_API_MODE", "chat_completions"),
        )

    def view(self) -> dict[str, Any]:
        config = self.load()
        row = self.store.get_model_settings()
        return {"config": config.model_dump(exclude={"api_key"}), "has_api_key": bool(config.api_key.get_secret_value()),
                "configured": config.configured, "revision": row["revision"] if row else 0,
                "source": "saved" if row else "environment", "providers": PROVIDERS}

    def candidate(self, update: SettingsUpdate) -> ModelConfig:
        current = self.load()
        key = ""
        if update.api_key and update.api_key.get_secret_value():
            key = update.api_key.get_secret_value()
        elif not update.clear_api_key:
            same_connection = update.provider == current.provider and update.api_base == current.api_base
            if current.api_key.get_secret_value() and not same_connection and not PROVIDER_BY_ID[update.provider].get("optional_key"):
                raise ValueError("切换厂商或 API 地址时，请重新填写该连接的 API Key。")
            if same_connection:
                key = current.api_key.get_secret_value()
        if len(key) > 8192:
            raise ValueError("API Key 过长。")
        data = update.model_dump(exclude={"api_key", "clear_api_key", "revision"})
        config = ModelConfig(**data, api_key=key)
        if not config.configured:
            raise ValueError("请填写模型名称、所需的 API 地址和 API Key。")
        if config.provider == "litellm" and "/" not in config.model:
            raise ValueError("其他厂商的模型名称请使用 provider/model 格式。")
        return config

    def save(self, update: SettingsUpdate) -> dict[str, Any]:
        config = self.candidate(update)
        key = config.api_key.get_secret_value()
        encrypted = self._cipher(create=True).encrypt(key.encode()).decode() if key else ""
        self.store.save_model_settings(config.model_dump(exclude={"api_key"}), encrypted, update.revision)
        return self.view()

# 模型连接配置

点击画布右上角“设置”进入 `/settings`。选择厂商，填写模型 ID、需要的 Base URL 与 API Key，点击“测试连接”，确认文本响应和工具调用均通过后点击“保存配置”。测试使用当前表单进行实际请求，不修改已保存配置，会产生厂商计费调用。

主 Agent、全部子 Agent 和任务重试共用一个模型连接及 API Key。各 Agent 的消息列表独立创建，子 Agent 的完整工具记录保存在 SQLite。任意画布有任务正在排队或运行时，保存按钮置灰，接口返回 HTTP 409；全部任务结束后可保存新配置。

## 厂商与 API

| 厂商选项 | 配置方式 |
| --- | --- |
| OpenAI | 模型 ID 与 API Key；支持 Responses 和 Chat Completions |
| Anthropic Claude | 模型 ID 与 API Key，由适配器转换消息和工具调用 |
| Google Gemini | 模型 ID 与 Gemini API Key |
| Azure OpenAI | 部署名称、资源 Base URL、API Key 与所需 API Version；支持 Responses 和 Chat Completions |
| 火山方舟 / Ark | 模型或接入点 ID、Ark API Key 与对应服务地址；预设为 Agent Plan 入口 |
| DeepSeek、阿里百炼、Moonshot / Kimi、智谱 / Z.ai、MiniMax | 模型 ID 与各自 API Key，可按账户服务地区修改 Base URL |
| 硅基流动 / SiliconFlow | 模型 ID 与 API Key，使用预设的 OpenAI 兼容地址 |
| xAI、Groq、Mistral、OpenRouter、Together AI、Cohere | 模型 ID 与 API Key，由厂商适配器处理请求 |
| Amazon Bedrock | Bedrock API Key 或服务端 AWS 凭据，在扩展参数中填写 `aws_region_name` |
| Google Vertex AI | 服务端 Google Cloud 凭据，在扩展参数中填写 `vertex_project` 和 `vertex_location` |
| Ollama | 本地模型名称与服务地址，API Key 可选 |
| 本地 OpenAI 兼容服务 | 模型名称与 Base URL，API Key 可选 |
| 自定义 OpenAI 兼容接口 | 对应厂商的 Base URL、模型 ID 与 API Key |
| 其他厂商 / LiteLLM | 模型使用 `provider/model` 格式，凭据按该厂商的服务端配置方式提供 |

Base URL 不应附加 `chat/completions`、`responses` 或 `messages`。Azure 需要填写资源地址。模型必须支持 function calling；厂商提供文本 API 并不保证每个模型都能执行绘图工具。切换到需要 API Key 的新连接时，需要重新填写该连接的密钥；切换到可选密钥的连接时，可以留空使用本地服务或服务端云凭据。

高级设置提供请求超时、API 类型、Azure API Version 和 JSON 扩展参数。扩展参数用于思考模式、地区等厂商选项；连接地址、凭据和回调设置由服务端专用字段管理。

厂商适配使用 LiteLLM SDK，当前固定版本为 `1.103.0`。本项目已使用 Ark Agent Plan 的 `ark-code-latest` 完成真实文本、工具调用、多轮编辑与并行子 Agent 验证。其他预设已检查 SDK 路由映射，尚未使用对应厂商账户进行真实请求验证，使用前应运行设置页的连接测试。

## 保存与环境变量

配置保存在应用 SQLite 数据库。API Key 使用 Fernet 加密后保存，浏览器读取设置时只获得是否已配置。加密文件默认位于数据库同一目录的 `model-settings.key`。保留原连接、API Key 输入为空时，保存会继续使用已有密钥；填写新密钥后保存会更新。

没有保存配置时，服务使用可选 `.env` 初始配置。可复制 `.env.example` 并按需要填写：

| 环境变量 | 用途 |
| --- | --- |
| `DRAW_AGENT_PROVIDER` | 设置页厂商 ID，默认 `ark` |
| `DRAW_AGENT_API_KEY` | 初始 API Key；兼容读取已有 `ARK_API_KEY` |
| `DRAW_AGENT_MODEL` | 初始模型名称 |
| `DRAW_AGENT_BASE_URL` | 初始 Base URL |
| `DRAW_AGENT_API_MODE` | `chat_completions` 或 `responses` |
| `DRAW_AGENT_API_VERSION` | Azure API Version |
| `DRAW_AGENT_REQUEST_PARAMETERS` | JSON 扩展参数 |
| `DRAW_AGENT_DB` | 数据库路径，默认 `runs/studio.sqlite3` |
| `DRAW_AGENT_SETTINGS_KEY_FILE` | 加密文件路径，默认随数据库目录保存 |

设置页保存后优先使用数据库配置。`.env`、数据库和加密文件属于服务端私有数据。部署备份需要同时保留数据库与加密文件，具体操作见[部署说明](deployment.md)。

运行中的服务可通过以下命令验证当前连接：

```bash
python -m scripts.probe_model
```

## 持续绘图与停止任务

应用不设置模型请求或绘图任务的时间上限。运行期间可以点击“停止”，取消当前任务及其子 Agent 请求；模型厂商或网络返回错误时，任务按错误处理流程结束。只有主 Agent 可以创建子 Agent，子 Agent 仅能使用绘图工具。

模型厂商返回 HTTP 429 时，适配器根据 `Retry-After` 或逐渐延长的随机间隔等待并重试，直至成功或用户停止；账户额度错误会直接报告。等待记录写入 SQLite，不加入主 Agent 的对话上下文。

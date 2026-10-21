# Model setup

[简体中文](model-settings.md) · **English**

Open **Settings** (`设置`) in the top-right corner of the canvas. Choose a provider and enter the model ID, API URL if required, and API key. Click **Test connection** (`测试连接`), then **Save** (`保存配置`) once the test passes.

The model must support both **image input and tool calling**. The connection test sends an actual image and verifies image recognition, tool calls, and the response after a tool result. It uses the current form without changing saved settings and may incur provider charges.

## Choose a connection

| Provider | Configuration |
| --- | --- |
| OpenAI | Model ID and API key. Responses and Chat Completions are supported. |
| Anthropic Claude | Model ID and API key. |
| Google Gemini | Model ID and Gemini API key. |
| Azure OpenAI | Deployment name, resource base URL, API key, and API version. Responses and Chat Completions are supported. |
| Volcano Engine / Ark | Model or endpoint ID, Ark API key, and service URL. The preset uses the Agent Plan endpoint. |
| DeepSeek, Alibaba Cloud, Moonshot / Kimi, Z.ai, MiniMax | Model ID and provider API key. Adjust the base URL for your account region if needed. |
| SiliconFlow | Model ID and API key, using its preset OpenAI-compatible URL. |
| xAI, Groq, Mistral, OpenRouter, Together AI, Cohere | Model ID and provider API key. |
| Amazon Bedrock | Bedrock API key or server-side AWS credentials. Set `aws_region_name` in the advanced parameters. |
| Google Vertex AI | Server-side Google Cloud credentials. Set `vertex_project` and `vertex_location` in the advanced parameters. |
| Ollama | Local model name and service URL. An API key is optional. |
| Local OpenAI-compatible service | Model name and base URL. An API key is optional. |
| Custom OpenAI-compatible endpoint | Provider base URL, model ID, and API key. |
| Other providers / LiteLLM | Use `provider/model` as the model name and configure that provider's server-side credentials. |

Enter the API base URL without appending `chat/completions`, `responses`, or `messages`. For Azure, use the resource URL.

A provider preset does not guarantee that every model supports the required capabilities. Run the connection test for the model you choose. Ark Agent Plan with `ark-code-latest` has been tested with actual image recognition, drawing edits, tool calls, and parallel drawing agents. Other presets have been checked for SDK routing but have not all been tested with live provider accounts.

## Save and change settings

The main agent, drawing agents, and retries share one model connection and API key. Their conversation histories remain separate.

Settings cannot be saved while any canvas has a running or queued task. Wait for tasks to finish or stop them before changing the connection.

For the current connection, leaving the API key field empty preserves its saved key. Enter a new key to replace it. When switching to another connection that requires a key, enter that connection's key again. Connections with optional keys may use a local service or server-side cloud credentials.

Advanced settings include the API mode, Azure API version, and JSON parameters for options such as reasoning or region. The service manages connection addresses and credentials through their dedicated fields.

## Storage and initial configuration

Settings are saved in the application database. API keys are encrypted with Fernet; the browser receives only whether a key has been configured. The encryption key file defaults to `model-settings.key` beside the database. Back up both files together. See [Deployment](deployment.en.md).

Before settings have been saved through the interface, the service can read an optional `.env` file. Copy `.env.example` and configure the values you need:

| Variable | Purpose |
| --- | --- |
| `DRAW_AGENT_PROVIDER` | Provider ID; defaults to `ark`. |
| `DRAW_AGENT_API_KEY` | Initial API key. Existing `ARK_API_KEY` values are also accepted. |
| `DRAW_AGENT_MODEL` | Initial model ID. |
| `DRAW_AGENT_BASE_URL` | Initial API base URL. |
| `DRAW_AGENT_API_MODE` | `chat_completions` or `responses`. |
| `DRAW_AGENT_API_VERSION` | Azure API version. |
| `DRAW_AGENT_REQUEST_PARAMETERS` | Additional parameters as JSON. |
| `DRAW_AGENT_DB` | Database path; defaults to `runs/studio.sqlite3`. |
| `DRAW_AGENT_SETTINGS_KEY_FILE` | Encryption key file path; defaults to the database directory. |

Saved database settings take priority over `.env` values. Keep `.env`, the database, and the encryption key private.

To check the saved connection against a running service:

```bash
python -m scripts.probe_model
```

This performs actual model requests and may incur provider charges.

## Running and stopping a drawing

The application does not impose a total drawing-task or model-request time limit. Click **Stop** (`停止`) to cancel the current task and its drawing agents.

When a provider returns HTTP 429, agents using the connection share a retry queue. The application observes `Retry-After` or waits with increasing randomized delays. The interface shows the waiting state, retry countdown, and elapsed time. You can stop waiting. Account-quota errors are reported directly.

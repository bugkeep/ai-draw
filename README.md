<div align="center">
  <h1>Draw Agent Studio</h1>
  <p>Draw and edit through conversation.</p>
  <p><a href="README.zh-CN.md">简体中文</a> · <strong>English</strong></p>
</div>

## Demos

**Draw a seaside lighthouse through conversation**

https://github.com/user-attachments/assets/7714f650-9fae-4703-bc8d-e0e7c9602668

**Create a research flowchart through conversation**

https://github.com/user-attachments/assets/d940e09e-315d-4c90-b445-78f5c335a306

<details>
<summary>Finish an AI sunflower sketch by hand</summary>

https://github.com/user-attachments/assets/a49c6592-7d0c-42b2-9ead-c513bdab30bf

</details>

## Features

- **Describe**: Create sketches, illustrations, and flowcharts from text.
- **Refine**: Move objects, change colors, delete, undo, and redo through conversation.
- **Draw by hand**: Select, drag, use the pen, or add text directly on the canvas.
- **Save your work**: Canvases save automatically. Export PNG, JPG, WebP, SVG, or editable Excalidraw files.

## Quick start

Requires **Python 3.11+** and **Node.js 20.19+**. On Windows, install Chrome as well.

### 1. Install and start

```bash
git clone https://github.com/bugkeep/ai-draw.git
cd ai-draw
python -m pip install -r requirements.txt
cd frontend-app
npm ci
npm run build
npx playwright install --with-deps chromium
cd ..
python run.py
```

For later launches, run `python run.py` from the project directory.

### 2. Connect a model

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and click **Settings** (`设置`) in the top-right corner.

1. Choose a provider and enter the model name, API URL, and API key.
2. Use a model with **image input and tool calling**, then click **Test connection** (`测试连接`).
3. Once the test passes, click **Save** (`保存配置`) and return to the canvas.

Choose OpenAI, Claude, Gemini, Ark, or another provider, or connect a local service or custom compatible endpoint. API usage is billed by your provider. [Model setup](docs/model-settings.en.md)

## Try these prompts

- "Draw a multimodal RAG research flowchart, grouped into data preparation, retrieval and generation, and evaluation and output."
- "Add a quality check and a feedback loop to the evaluation section."
- "Draw a seaside lighthouse under moonlight, with a small sailboat and stars."
- "Make the sea a deeper blue-green and brighten the lighthouse light."

## Feedback and contributions

Share your drawings, suggest ideas, or report problems in [Issues](https://github.com/bugkeep/ai-draw/issues).

[Deployment](docs/deployment.en.md) · [Contributing](CONTRIBUTING.md) · [Development](docs/development.en.md) · [MIT License](LICENSE)

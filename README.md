# Draw Agent Studio

Draw Agent Studio 是一个通过连续对话编辑同一张画布的开源绘图应用。用户可以描述图形、接着说“选中它”“向右移动 80 像素”“改成蓝色”，也可以直接使用 Excalidraw 选择和调整对象。Agent 的画笔轨迹按帧显示，完成后仍是可编辑的 Excalidraw 对象。

目前支持矩形、椭圆、菱形、文字、线条、箭头和画笔对象，以及选择、移动、修改、删除、撤销和重做。常见的明确指令由本地绘图命令处理；复杂场景由主 Agent 调用工具创建子 Agent，并行准备动作。子 Agent 可以复用、继续接收输入、关闭和恢复，每条输入具有独立编号。主 Agent 审阅结构化摘要，再接受结果并逐步提交。子 Agent 的完整工具记录和上下文保存在 SQLite。

绘图动作数量与模型请求持续时间由任务需要决定。用户可主动停止任务；子 Agent 只执行绘图工具，不能创建其他 Agent。

## 运行

需要 Python 3.11+ 和 Node.js 20.19+。建议创建独立 Python 环境后安装依赖：

```bash
python -m pip install -r requirements.txt
cd frontend-app
npm ci
npm run build
cd ..
python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，点击右上角“设置”，填写模型厂商、模型名称、API 地址和 API Key，测试连接后点击“保存配置”。主 Agent 和全部子 Agent 共用该连接，各自拥有独立的会话与上下文。任意画布有任务正在运行或排队时，保存按钮置灰，服务端同时拒绝修改配置。

设置页提供 OpenAI、Anthropic、Gemini、Azure OpenAI、Ark、DeepSeek、百炼、Kimi、智谱、MiniMax、硅基流动、OpenRouter 等厂商预设，以及 Ollama、本地服务和自定义 OpenAI 兼容接口。接入方法与验证范围见[模型配置说明](docs/model-settings.md)。API Key 在服务端加密保存，读取设置时仅返回是否已配置。

前端开发时运行 `cd frontend-app && npm run dev`，Vite 会代理 `/api` 和 WebSocket。面向其他用户部署时，请参考[部署说明](docs/deployment.md)。

## 试用

1. 输入“画一个蓝色圆形，名字叫主圆”。
2. 接着输入“把它向右移动 80 像素”“选中刚才的圆形”“把它改成红色”。
3. 输入“撤销”“重做”，或者点击画布上方的按钮。
4. 输入“用画笔画一张笑脸，展示作画过程”，观察轮廓、眼睛和嘴巴逐步出现。
5. 在 Excalidraw 中直接选择、拖动或修改对象，再继续对话。

每个会话有独立画布、消息、任务、实时事件和撤销历史。切换画布或打开多个窗口时，各自显示所属会话的内容；同一会话内的指令按顺序执行，当前 Agent 上下文不包含后续排队任务的指令。服务重启后，数据仍保存在 `runs/studio.sqlite3`，该路径可通过 `DRAW_AGENT_DB` 配置。

点击“新建”后可直接输入画布名称，按 Enter 或点击勾号保存，按 Escape 或点击取消关闭名称编辑。已有画布条目右侧的铅笔按钮可修改名称，名称会同步更新到画布标题并保存到数据库。左侧画布列表与右侧对话分别滚动，画布和输入框保留在窗口内。

条目右侧的删除按钮会打开确认窗口。删除会移除该画布的图形、对话、任务、工具记录和历史；运行或排队中的任务需要先停止。删除当前画布后自动打开其他画布，列表为空时创建空白画布。同一画布的其他窗口会接收删除通知并切换。

## 结构

| 目录 | 职责 |
| --- | --- |
| `frontend-app/` | React、Excalidraw、绘图对话和实时事件展示 |
| `studio/actions.py` | 绘图动作校验与原生 Excalidraw 对象构建 |
| `studio/agents.py`、`studio/agent_tools.py` | 主 Agent 工具循环所用上下文、模型请求和工具定义 |
| `studio/agent_manager.py` | 子 Agent 身份、输入排队、并发执行、等待和关闭恢复 |
| `studio/agent_store.py` | Agent 私有上下文、执行检查点、调用幂等和绘图结果 |
| `studio/llm.py`、`studio/settings.py` | 多厂商模型请求、连接验证和加密配置 |
| `studio/runtime.py` | 任务队列、逐项提交、画笔过程和取消操作 |
| `studio/store.py` | SQLite 会话、画布版本、历史记录和工具记录 |
| `studio/api.py` | HTTP API 与画布 WebSocket 事件 |
| `scripts/` | 真实操作成功率与延迟评测 |

会话、任务和绘图提交的处理方法见[架构说明](docs/studio-architecture.md)，工具协议与状态转换见[子 Agent 生命周期](docs/agent-lifecycle.md)。前端使用 [Excalidraw](https://github.com/excalidraw/excalidraw)，许可证为 MIT；本项目许可证见 [LICENSE](LICENSE)。
参与开发请阅读[贡献说明](CONTRIBUTING.md)，需求与故障通过 GitHub Issue 管理，代码变更通过 Pull Request 审阅。

## 验证

安装测试依赖后运行：

```bash
python -m pip install -r requirements-dev.txt
cd frontend-app
npm ci
npm run build
npm audit --omit=dev
cd ..
python -m pytest -q
```

运行连续编辑评测：

```bash
python -m scripts.evaluate_studio
```

启动服务并在设置页配置真实模型后，可运行连接验证、多轮指代与并行子 Agent 评测：

```bash
python -m scripts.probe_model
python -m scripts.evaluate_agent_edit
python -m scripts.evaluate_multiagent
python -m scripts.evaluate_transformer
```

生命周期评测从 `.env` 或 `DRAW_AGENT_*` 环境变量读取模型连接，在独立 SQLite 数据库中检查排队、关闭恢复、提交幂等和同一对象的后续编辑：

```bash
python -m scripts.evaluate_agent_lifecycle
```

对组合绘图评测创建的画布，可以继续检查主 Agent 复用原子 Agent、原对象编辑、撤销重做及关闭恢复：

```bash
python -m scripts.evaluate_agent_reuse --session-id SESSION_ID
```

评测逐项检查画布中的对象和任务结果，并记录成功率、耗时与子 Agent 数量。`evaluate_agent_edit` 检查真实模型能否根据上一轮对话修改同一个对象。结果写入 Git 忽略的 `runs/`。Windows 环境的 pytest 临时目录若无写入权限，可追加 `--basetemp runs/pytest-studio`。

本次真实运行的场景、成功率及耗时见[评测记录](docs/evaluation.md)。

服务运行时可以录制浏览器中的真实操作过程：

```bash
cd frontend-app
npx playwright install ffmpeg
node scripts/record_demo.js
```

脚本使用本机 Chrome，依次创建、移动图形并展示画笔轨迹，输出 `runs/draw-agent-demo.webm`。其他平台可安装 Playwright 浏览器，或设置 `CHROME_PATH` 指向 Chromium 可执行文件。

浏览器端检查需要运行中的服务：

```bash
cd frontend-app
node scripts/browser_smoke.js
node scripts/settings_smoke.js
node scripts/sessions_smoke.js
node scripts/deletion_smoke.js
```

检查覆盖真实 Excalidraw 手绘、撤销、重做、刷新恢复、PNG 导出，以及设置导航、配置保存、密钥不回传、任务期间禁止保存、画布切换和双窗口隔离。`settings_smoke.js` 会保留已有模型连接并保存一次配置；未配置模型的测试环境会保存本地服务预设，全部绘图检查使用应用内的明确指令。已配置真实模型时，可设置环境变量 `DRAW_AGENT_TEST_MODEL=1`，同时点击设置页“测试连接”并验证实际模型响应。

`sessions_smoke.js` 检查长列表、长对话、独立滚动、名称编辑、刷新恢复、多窗口名称更新和移动窗口尺寸。此脚本会创建至少 36 个会话及 25 个绘图任务，应在使用独立 `DRAW_AGENT_DB` 的测试服务执行；通过 `DRAW_AGENT_URL` 指定测试服务地址。

`deletion_smoke.js` 检查确认与取消、删除当前或其他画布、双窗口通知、运行任务保护、停止后删除和刷新恢复。在空白测试数据库中运行时，也会检查删除最后一个画布后的空白画布创建。`evaluate_transformer` 使用真实模型绘制 Transformer 流程图，再修改选中标题并检查其他对象、撤销和重做。

## 当前边界

服务端按单用户使用方式设计。对外开放时需要在反向代理设置身份验证和 HTTPS。会话之间提供应用内的数据隔离；多人账户权限与实时协作需要独立设计。

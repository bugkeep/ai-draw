# Draw Agent Studio

Draw Agent Studio 是一个通过连续对话编辑同一张画布的开源绘图应用。用户可以描述图形、接着说“选中它”“向右移动 80 像素”“改成蓝色”，也可以直接使用 Excalidraw 选择和调整对象。Agent 的画笔轨迹按帧显示，完成后仍是可编辑的 Excalidraw 对象。

目前支持矩形、椭圆、菱形、文字、线条、箭头和画笔对象，以及选择、移动、修改、删除、撤销和重做。常见的明确指令由本地绘图命令处理；复杂场景由主 Agent 安排子 Agent 并行准备动作。主 Agent 审阅子 Agent 返回的 JSON，再按画布版本依次提交。子 Agent 的工具记录保存在 SQLite，主 Agent 的上下文只包含画布摘要、最近对话和子 Agent 结果。

## 运行

需要 Python 3.11+、Node.js 20.19+ 和 Ark Agent Plan API Key。将 `.env.example` 复制为项目根目录的 `.env`，填写 `ARK_API_KEY`。API Key 只由服务端读取；`.env` 已被 Git 忽略。

```bash
python -m pip install -r requirements.txt
cd frontend-app
npm ci
npm run build
cd ..
python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。前端开发时运行 `cd frontend-app && npm run dev`，Vite 会代理 `/api` 和 WebSocket。面向其他用户部署时，请参考 [部署说明](docs/deployment.md)。

## 试用

1. 输入“画一个蓝色圆形，名字叫主圆”。
2. 接着输入“把它向右移动 80 像素”“选中刚才的圆形”“把它改成红色”。
3. 输入“撤销”“重做”，或者点击画布上方的按钮。
4. 输入“用画笔画一张笑脸，展示作画过程”，观察轮廓、眼睛和嘴巴逐步出现。
5. 在 Excalidraw 中直接选择、拖动或修改对象，再继续对话。

每个会话有独立画布。服务重启后，画布、对话、任务状态和工具记录仍保存在 `runs/studio.sqlite3`。该路径可通过 `DRAW_AGENT_DB` 配置。

## 结构

| 目录 | 职责 |
| --- | --- |
| `frontend-app/` | React、Excalidraw、绘图对话和实时事件展示 |
| `studio/actions.py` | 绘图动作校验与原生 Excalidraw 对象构建 |
| `studio/agents.py` | 主 Agent 规划、子 Agent 工具调用和结果审阅 |
| `studio/runtime.py` | 任务队列、逐项提交、画笔过程和取消操作 |
| `studio/store.py` | SQLite 会话、画布版本、历史记录和工具记录 |
| `studio/api.py` | HTTP API 与画布 WebSocket 事件 |
| `scripts/` | 真实操作成功率与延迟评测 |

会话、任务和绘图提交的处理方法见[架构说明](docs/studio-architecture.md)。前端使用 [Excalidraw](https://github.com/excalidraw/excalidraw)，许可证为 MIT；本项目许可证见 [LICENSE](LICENSE)。
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

使用已配置的真实 Ark 服务运行连续编辑评测：

```bash
python -m scripts.evaluate_studio
```

启动服务后可运行并行子 Agent 评测：

```bash
python -m scripts.evaluate_agent_edit
python -m scripts.evaluate_multiagent
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

浏览器端检查可运行 `cd frontend-app && node scripts/browser_smoke.js`。脚本在真实 Excalidraw 画布上手绘矩形，检查保存、撤销、重做、刷新恢复和 PNG 导出。

## 当前边界

服务端按单用户画布设计。对外开放时需要在反向代理设置身份验证和 HTTPS。文本对话、原生 Excalidraw 图形以及 PNG 导出可用；语音输入和多人实时协作尚未接入此编辑器。

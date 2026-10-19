# 部署 Draw Agent Studio

安装 Python 3.11+ 与 Node.js 20.19+，在项目根目录创建 `.env`，填写 `ARK_API_KEY`。`DRAW_AGENT_BASE_URL` 默认使用 Ark Agent Plan 的 OpenAI 兼容入口 `https://ark.cn-beijing.volces.com/api/plan/v3`，默认模型为 `ark-code-latest`。

```bash
python -m pip install -r requirements.txt
cd frontend-app
npm ci
npm run build
cd ..
python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000
```

服务同一端口提供前端、HTTP API 和 WebSocket。对外部署时，让反向代理终止 HTTPS 并转发 WebSocket，给整个站点设置身份验证；Uvicorn 保持监听本机地址。数据库默认位于 `runs/studio.sqlite3`，设置 `DRAW_AGENT_DB` 可改变位置。备份时复制 SQLite 数据库及其 `-wal` 和 `-shm` 文件，或使用 SQLite 的备份接口。

`GET /api/studio/health` 返回模型是否已配置。可运行 `python -m scripts.evaluate_studio` 检查真实连续编辑；服务启动后运行 `python -m scripts.evaluate_multiagent` 检查子 Agent 并行准备与逐项提交。评测会创建新会话并产生模型调用。

# 部署 Draw Agent Studio

安装 Python 3.11+ 与 Node.js 20.19+。建议使用独立 Python 环境，安装的 Uvicorn 包含 WebSocket 运行依赖。

```bash
python -m pip install -r requirements.txt
cd frontend-app
npm ci
npm run build
cd ..
python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000
```

访问首页，点击“设置”并保存模型连接。厂商 API 适配、云凭据和可选环境配置见[模型配置说明](model-settings.md)。主 Agent 和子 Agent 使用同一个模型与 API Key。任意绘图任务运行或排队时，设置页和保存接口均阻止修改配置。

服务同一端口提供前端、HTTP API 和 WebSocket，使用单个 Uvicorn worker。画布任务队列和实时事件订阅由当前进程管理。对外部署时，让反向代理终止 HTTPS 并转发 WebSocket，给整个站点设置身份验证；Uvicorn 保持监听本机地址。

数据库默认位于 `runs/studio.sqlite3`，设置 `DRAW_AGENT_DB` 可改变位置。模型配置与加密后的 API Key 保存在同一数据库；加密文件默认位于数据库目录的 `model-settings.key`，可通过 `DRAW_AGENT_SETTINGS_KEY_FILE` 指定其他位置。备份应同时包含数据库和加密文件。运行期间使用 SQLite 的备份接口；停止服务后可复制数据库及其 `-wal` 和 `-shm` 文件。加密文件缺失时，应从备份恢复后启动服务。

`GET /api/studio/health` 返回模型是否已配置。服务启动后运行 `python -m scripts.probe_model` 验证实际文本与工具调用；运行 `python -m scripts.evaluate_multiagent` 检查子 Agent 并行准备与逐项提交。模型评测会创建新会话并产生计费调用。`python -m scripts.evaluate_studio` 检查明确指令的连续编辑行为。

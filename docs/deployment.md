# 部署 Draw Agent Studio

安装 Python 3.11+ 与 Node.js 20.19+。建议使用独立 Python 环境，安装的 Uvicorn 包含 WebSocket 运行依赖。

```bash
python -m pip install -r requirements.txt
cd frontend-app
npm ci
npm run build
npx playwright install --with-deps chromium
cd ..
python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000
```

Windows 图片服务默认使用本机 Chrome；其他系统使用 Playwright Chromium。可以通过 `CHROME_PATH` 指定其他 Chromium 可执行文件。Node.js、浏览器和 `frontend-dist/render.html` 均为运行时依赖；图片服务按需启动并复用浏览器。构建或浏览器缺失时，任务明确报告图片服务错误。

访问首页，点击“设置”并保存支持图片输入与 function calling 的模型连接。连接测试会向模型提供随机图形图片，核对识别结果、工具调用及后续文本响应。厂商 API 适配、云凭据和可选环境配置见[模型配置说明](model-settings.md)。主 Agent 和子 Agent 使用同一个模型与 API Key。任意绘图任务运行或排队时，设置页和保存接口均阻止修改配置。

服务同一端口提供前端、HTTP API 和 WebSocket，使用单个 Uvicorn worker。画布任务队列和实时事件订阅由当前进程管理。对外部署时，让反向代理终止 HTTPS 并转发 WebSocket，给整个站点设置身份验证；Uvicorn 保持监听本机地址。

数据库默认位于 `runs/studio.sqlite3`，设置 `DRAW_AGENT_DB` 可改变位置。模型配置与加密后的 API Key 保存在同一数据库；加密文件默认位于数据库目录的 `model-settings.key`，可通过 `DRAW_AGENT_SETTINGS_KEY_FILE` 指定其他位置。备份应同时包含数据库和加密文件。运行期间使用 SQLite 的备份接口；停止服务后可复制数据库及其 `-wal` 和 `-shm` 文件。加密文件缺失时，应从备份恢复后启动服务。

数据库同时保存画布检查图片、原始工具记录、检索索引和 Agent 工作记忆。服务启动时自动创建记忆表，已有工具与消息在检索时建立索引，无需额外搜索服务或模型连接。长期作画时，需要根据实际作品和检查次数安排磁盘空间。删除会话会一并删除其检查图片、记忆索引和工作记忆；图片读取接口检查会话归属，并使用私有、不缓存的响应。

参考图片、查看记录与完整轮廓路径也单独保存在同一 SQLite 数据库中，启动时自动建立对应表；备份该数据库即可保留这些内容。Pillow、scikit-image 与 Skan 随 `requirements.txt` 安装，参考图功能使用相同模型连接提供实际图片，无需增加模型或搜索服务 API Key。免费公共来源可能限制访问，工具记录实际错误，来源可用性需在部署网络中验证。会话删除会同时清理其参考图片与轮廓缓存。

`GET /api/studio/health` 返回模型是否已配置。服务启动后运行 `python -m scripts.probe_model` 验证实际文本与工具调用；运行 `python -m scripts.evaluate_multiagent` 检查子 Agent 并行准备与逐项提交。模型评测会创建新会话并产生计费调用。`python -m scripts.evaluate_studio` 检查明确指令的连续编辑行为。

更新代码并构建前端后，需要停止当前 Uvicorn 进程并重新启动。页面会核查 `/api/studio/health` 的 `api_revision`；服务尚未加载匹配版本时显示提示，暂时禁止新建、发送指令、重命名和删除，重新连接后自动恢复这些操作。

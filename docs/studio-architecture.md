# Draw Agent Studio 架构

## 职责

| 组件 | 输入 | 输出与职责 |
| --- | --- | --- |
| Excalidraw 编辑器 | 用户手势、画布事件 | 原生对象、选择状态和逐帧画笔展示 |
| 会话 API | 对话文本、画布快照 | 会话、任务、版本化画布和事件连接 |
| 主 Agent | 最近对话、画布摘要、用户目标 | 绘图方案、子任务和子 Agent 结果审阅 |
| 子 Agent | 指定目标、相关对象与受限工具 | 经过校验的动作 JSON；无权直接修改共享画布 |
| 绘图执行器 | 主 Agent 接受的动作 | 按顺序提交、对象版本检查、事件与撤销记录 |
| SQLite | 文档、对话、任务和工具记录 | 重启恢复及事件追溯 |

主 Agent 可以安排最多三个子 Agent 同时准备独立的画面部分。每个子 Agent 使用隔离的画布副本，调用 `create_elements`、`create_element`、`select_elements`、`move_elements`、`update_elements`、`delete_elements` 或 `inspect_scene`。工具参数在执行位置校验。返回给主 Agent 的 JSON 包含摘要、动作和成功状态；每轮模型响应及工具结果写入 `tool_logs`。主 Agent 审阅 JSON 后，绘图执行器按序提交。对同一对象的并发修改通过元素版本检查拒绝覆盖。

`create_elements` 一次可以准备最多 24 个原生对象。子 Agent 同时请求模型；某个子任务失败时可用 `DRAW_AGENT_RECOVERY_MODEL` 重新准备。主 Agent 审阅的 JSON 只保留元素名称、类型、位置和动作数量，画笔点列及完整调用记录保留在 SQLite。默认模型为 `ark-code-latest`，可分别用 `DRAW_AGENT_MODEL` 和 `DRAW_AGENT_CHILD_MODEL` 配置主 Agent 与子 Agent；`DRAW_AGENT_THINKING` 默认关闭深度思考以缩短等待时间。

画布由服务端保存。Excalidraw 的手动修改携带文档版本提交；版本变化时接口返回 HTTP 409，前端重新读取画布。绘图执行器每提交一步就发出 `scene` 事件。画笔对象在提交前发出多个 `stroke_progress` 事件，前端用临时对象显示轨迹，最后由正式 `scene` 事件替换。WebSocket 重连时带上最后收到的事件编号，从 SQLite 读取遗漏事件。

撤销与重做由 SQLite 保存最近 50 次内容修改。常见明确指令由 `studio/fast_path.py` 准备动作并经过同一个绘图执行器。服务端启动时读取 `.env`，API Key 仅保留在服务端环境中。

## API

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/studio/health` | 服务和模型配置状态 |
| `GET/POST /api/studio/sessions` | 列出与创建会话 |
| `GET /api/studio/sessions/{id}` | 读取会话、对话、画布和任务 |
| `POST /api/studio/sessions/{id}/messages` | 排队执行一条绘图指令 |
| `PUT /api/studio/documents/{id}/scene` | 保存手动编辑，检查版本 |
| `POST /api/studio/documents/{id}/history` | 撤销或重做 |
| `GET /api/studio/tasks/{id}` | 查询任务状态和耗时 |
| `POST /api/studio/tasks/{id}/cancel` | 停止任务 |
| `GET /api/studio/tasks/{id}/logs` | 查看 SQLite 工具记录 |
| `WS /api/studio/documents/{id}/events?after=N` | 接收画布、任务、对话和画笔事件 |

`runs/studio.sqlite3` 包含用户对话及完整工具记录，应按应用数据保护。运行评测时 `runs/` 也保存结果文件。

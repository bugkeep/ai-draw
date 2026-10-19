# Draw Agent Studio 架构

## 职责

| 组件 | 输入 | 输出与职责 |
| --- | --- | --- |
| Excalidraw 编辑器 | 用户手势、画布事件 | 原生对象、选择状态和逐帧画笔展示 |
| 会话 API | 对话文本、画布快照 | 会话、任务、版本化画布和事件连接 |
| 模型配置与请求 | 设置表单、服务端凭据 | 单一共享连接、多厂商 API 适配、连接测试与加密保存 |
| 主 Agent | 最近对话、画布摘要、用户目标 | 绘图方案、子任务和子 Agent 结果审阅 |
| 子 Agent | 指定目标、相关对象与受限工具 | 经过校验的动作 JSON；无权直接修改共享画布 |
| 绘图执行器 | 主 Agent 接受的动作 | 按顺序提交、对象版本检查、事件与撤销记录 |
| SQLite | 文档、对话、任务和工具记录 | 重启恢复及事件追溯 |

主 Agent 根据绘图目标安排子 Agent 同时准备独立的画面部分。每个子 Agent 使用隔离的画布副本，调用 `create_elements`、`create_element`、`select_elements`、`move_elements`、`update_elements`、`delete_elements` 或 `inspect_scene`。子 Agent 无权创建其他 Agent 或继续派发子任务，运行时逐次检查工具名称和参数。返回给主 Agent 的 JSON 包含摘要、动作和成功状态；每轮模型响应及工具结果写入 `tool_logs`。主 Agent 审阅 JSON 后，绘图执行器按序提交。对同一对象的并发修改通过元素版本检查拒绝覆盖。

主 Agent 的动作列表、子 Agent 累计准备动作和 `create_elements` 的对象列表均按完整内容处理。批量工具在应用到隔离画布前校验全部参数，几何数据或对象引用无效时返回校验问题。子 Agent 同时请求模型；某个子任务失败时使用同一连接重新准备一次。主 Agent 审阅的 JSON 只保留元素名称、类型、位置和动作数量，画笔点列及完整调用记录保留在 SQLite。主 Agent、子 Agent 和重试调用共用设置页保存的模型及 API Key，每个 Agent 独立创建消息列表。模型请求通过 LiteLLM 适配各厂商 API。

画布由服务端保存。Excalidraw 的手动修改携带文档版本提交；版本变化时接口返回 HTTP 409，前端重新读取画布。绘图执行器每提交一步就发出 `scene` 事件。画笔对象在提交前发出多个 `stroke_progress` 事件，前端用临时对象显示轨迹，最后由正式 `scene` 事件替换。WebSocket 重连时带上最后收到的事件编号，从 SQLite 读取遗漏事件。

撤销与重做由 SQLite 保存最近 50 次内容修改。常见明确指令由 `studio/fast_path.py` 准备动作并经过同一个绘图执行器。

每个会话关联独立文档，文档拥有自己的执行锁、事件订阅和历史记录。同一画布内的任务按顺序执行；构建 Agent 上下文时按任务创建顺序读取本次及此前任务的消息，排除后续排队指令。不同画布可以同时执行。前端切换会话时关闭旧连接、清理待保存快照，并使用会话加载编号与文档 ID 校验异步返回及编辑器回调。读取会话时同时返回事件编号，实时连接从该编号继续读取。

模型设置保存在 SQLite，加密文件单独保存在服务端。已有保存配置优先于可选 `.env` 初始配置。读取设置仅返回 API Key 是否存在。设置页每秒查询全局任务状态；任何任务运行或排队时禁止保存，后端在同步写入前再次检查。配置版本用于拒绝其他页面覆盖已更新的设置。

## API

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/studio/health` | 服务、API 版本和模型配置状态 |
| `GET /api/studio/settings` | 读取公开配置、厂商列表及全局任务状态 |
| `PUT /api/studio/settings` | 校验配置版本并加密保存；绘图期间返回 HTTP 409 |
| `POST /api/studio/settings/test` | 实际调用模型验证文本与工具调用能力 |
| `GET/POST /api/studio/sessions` | 列出与创建会话 |
| `GET /api/studio/sessions/{id}` | 读取会话、对话、画布和任务 |
| `PATCH /api/studio/sessions/{id}` | 修改会话与关联画布名称，保留图形、对话、任务和撤销历史 |
| `DELETE /api/studio/sessions/{id}` | 删除该会话及其画布、消息、任务、日志和事件；有运行或排队任务时返回 HTTP 409 |
| `POST /api/studio/sessions/{id}/messages` | 排队执行一条绘图指令 |
| `PUT /api/studio/documents/{id}/scene` | 保存手动编辑，检查版本 |
| `POST /api/studio/documents/{id}/history` | 撤销或重做 |
| `GET /api/studio/tasks/{id}` | 查询任务状态和耗时 |
| `POST /api/studio/tasks/{id}/cancel` | 停止任务 |
| `GET /api/studio/tasks/{id}/logs` | 查看 SQLite 工具记录 |
| `WS /api/studio/documents/{id}/events?after=N` | 接收画布、任务、对话和画笔事件 |

`runs/studio.sqlite3` 包含用户对话及完整工具记录，应按应用数据保护。运行评测时 `runs/` 也保存结果文件。

名称修改通过独立的 `session` 事件通知当前画布的窗口，不增加绘图版本或撤销记录。会话列表与对话区域各自限制高度并处理滚动，编辑器使用窗口剩余空间。

删除在一个 SQLite 事务中移除关联数据，提交成功后向该文档的订阅者发送 `session_deleted` 通知并关闭连接。前端清理旧画布的待保存内容与事件连接，打开其他会话；列表为空时创建空白画布。断开期间错过通知的窗口会在重连时检查会话是否存在。名称保存与删除只作用于指定会话。

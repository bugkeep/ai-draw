# 检查真实 Agent 上下文

每轮 harness 实验结束后，按实际请求顺序检查完整上下文：角色提示、当前输入与 DrawSpec、参考图、当前草稿、记忆、工具调用与返回、独立评审，以及对应模型回复。检查时同时查看全部工具定义。仅统计 token 数量或阅读最终回复不足以说明 Agent 根据什么信息作出了决定。

`scripts.inspect_agent_contexts` 使用 Python 标准 profiling 接口，在 `ModelGateway.complete` 的实际调用入口记录参数，并记录实际返回的模型响应。它不替换 Gateway 方法，不改变模型输入，不执行返回的工具调用。使用它启动原有评测时，工具继续由该评测的原运行时执行。

记录保存于单独的 SQLite 数据库，包含消息原文、图片 data URL、工具定义、调用来源、任务编号、模型名称、用量和回复。记录对象是项目 Gateway 的 Python 参数，第三方 SDK 的后续传输转换不属于该记录。新数据库与输出目录必须不存在。

## 记录一次独立实验

先确认目标评测使用独立作品数据库；本工具本身不会改变被启动模块的数据库选择。

```powershell
python -m scripts.inspect_agent_contexts --database runs/context-capture.sqlite3 --output-dir runs/context-capture --module scripts.evaluate_agent_lifecycle -- --database runs/context-lifecycle.sqlite3 --output runs/context-lifecycle.json
```

`--module` 后面使用评测模块名称；`--` 后面的参数原样传给该模块。正常结束或抛出异常时，记录器恢复原有 profiling 状态并导出已经记录的请求。没有返回响应的请求明确标记为未完成。发生异常仍按原异常退出，不把不完整记录计为成功任务。

## 查看已有记录

```powershell
python -m scripts.inspect_agent_contexts --database runs/context-capture.sqlite3 --output-dir runs/context-capture-export
```

此命令只读打开已有记录数据库，不发送模型请求。

- `index.html`：按请求顺序浏览所有上下文，包含实际图片、完整文字、工具定义及回复。
- `request-NNNN.json`：该次模型调用的完整 messages 与 tools，可用于逐项核对。
- `request-NNNN-response.json`：对应的实际模型返回，包括用量与结束原因。
- `request-NNNN-text.txt`：文字检查版本，图片位置保留哈希；完整图片仍在 JSON 与 HTML 中。
- `manifest.json`：消息角色、结构化字段、字符数量、图片哈希、实际用量与工具配对检查。

导出时重新计算请求哈希，确认消息与工具定义能够从记录中完整恢复。HTML 中的普通文字经过转义；所有文字保留原值，图片按实际请求展示。记录属于实验原始数据，默认保存于 Git 忽略的 `runs/`。

## 根据上下文定位问题

检查当前要求是否明确、历史完成声明是否仍适用于当前图片、历史对象编号是否仍存在、工具错误是否完整进入后续请求，以及重复读取是否已经提供所需信息。将评审的观察与绘法建议分别理解，并核对它们的图片、场景和任务来源。

工作记忆为空、存在历史绑定或发生重复查询，本身不证明作画失败的原因。需要沿着实际输入、模型回复、工具返回及画布变化核查，并用相同任务和冻结评审验证候选。观察器增加实验记录开销，延迟比较的两组应采用相同记录方式。

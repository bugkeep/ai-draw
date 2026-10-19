# 真实操作评测

评测日期：2026 年 10 月 19 日。环境为 Windows、Python 3.13、Node.js 24、Chrome，实际模型为 Ark Agent Plan `ark-code-latest`。服务、SQLite 和浏览器均在本机运行。本页记录 API 4 的检查结果，完整工具过程及作品保存在本机 Git 忽略的 `runs/`。

## 自动化与浏览器检查

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| 后端检查 | 51/51 通过，16.51 秒 | 实际 SQLite、原生曲线、路径修改、输入顺序、保存恢复、提交幂等、工具错误反馈、共享恢复队列、画布归属 |
| 浏览器完整检查 | 45/45 通过 | 编辑器 5 项、导出 13 项、设置 8 项、滚动与名称 9 项、删除 10 项 |
| 运行状态与停止 | 6/6 通过 | 实际模型请求、用时更新、刷新恢复、停止及重复停止 |
| 已读取来源展示 | 4/4 通过 | 实际研究任务的来源链接、刷新恢复及其他画布的资料归属 |
| 前端构建与依赖审计 | 通过 | `npm run build`；生产依赖审计未报告漏洞 |

浏览器导出检查实际下载 PNG、JPG、WebP、SVG 和 Excalidraw 文件，解析文件内容，并检查图片尺寸、透明背景、名称和小尺寸窗口。PNG 另外检查 2 倍与 4 倍分辨率。设置检查不包含额外模型连接探测；真实模型调用由下列评测覆盖。

后端检查仍有一条 Starlette/AnyIO 的弃用提示。前端构建仍有打包体积提示。两者均保留在检查输出中。

```bash
python -m pytest -q
python -m scripts.verify_browser --database runs/browser-verification.sqlite3
python -m scripts.verify_browser --database runs/activity-verification.sqlite3 --scripts activity_smoke.js
```

浏览器脚本使用生产应用、独立数据库和系统分配的本地端口，检查结束后正常关闭测试服务。`activity_smoke.js` 需要环境变量中配置的真实模型连接。

## 人物素描与连续编辑

浏览器只发送“画一幅蒙娜丽莎素描图”，录制完整操作并通过导出窗口下载作品。最近一次得到 130 个原生 Excalidraw 对象，脸部、双眼、鼻子、嘴唇、头发、双手、衣服和背景的 11 项结构检查全部通过；图形中没有图片对象。

| 指标 | 本次记录 |
| --- | --- |
| 首次正式画布更新 | 34.29 秒 |
| 完整任务 | 453.14 秒，即 7 分 33 秒 |
| 模型响应 | 16 次 |
| HTTP 429 恢复 | 3 次 |
| 工具错误 | 0 次 |
| 原生对象 | 130 个 |

记录位于 `runs/portrait-current-evaluation.json`、`runs/portrait-current-evaluation.excalidraw`、`runs/portrait-current.png` 和 `runs/portrait-current.webm`。会话编号为 `5caca9db3d0e4580a7c0a54cba3101c1`，任务编号为 `8629262c7900418ebac24511c5a7d926`，数据库为 `runs/portrait-current-real-1.sqlite3`。

结构检查主要核对原生对象及部位名称，不能判定人物相似程度、比例、线条表现和整体画面质量。评测明确将 `visual_acceptance` 保持为 `pending_user_review`。同日另一次 135 个对象的生成未包含背景，没有通过完整结构检查；模型结果仍存在变化，不能据单次通过承诺任意提示词的成图质量。

连续编辑使用另一份实际模型生成作品：选择嘴部路径后，让 Agent 调整微笑。17.39 秒完成修改，7 项检查通过，包括使用 `reshape_path`、保留对象编号、其他路径保持一致，以及撤销和重做。记录为 `runs/portrait-edit-real-2.json`。

生成与复查命令：

```bash
python -m scripts.evaluate_portrait --url http://127.0.0.1:8001 --output runs/portrait-evaluation.json
python -m scripts.evaluate_portrait --database runs/portrait-current-real-1.sqlite3 --session-id 5caca9db3d0e4580a7c0a54cba3101c1 --task-id 8629262c7900418ebac24511c5a7d926 --output runs/portrait-recheck.json
python -m scripts.verify_browser --database runs/portrait-recording.sqlite3 --scripts record_portrait.js
```

录制命令会使用真实模型新建作品，需要模型配置。`DRAW_AGENT_RECORDING_NAME` 可以指定录像和导出文件的名称前缀。

## 多题材绘图

题目及人工画面标准保存在 `evaluations/drawing-quality.json`。五个题目均使用真实模型，分别生成独立画布，工具错误及模型重试均为零。

| 题材 | 原生对象 | 首次正式更新 | 完整任务 | 结构检查 |
| --- | --- | --- | --- | --- |
| 水墨山水 | 227 | 60.65 秒 | 353.68 秒 | 通过 |
| 戴眼镜、手持杯子的人物 | 145 | 41.67 秒 | 280.84 秒 | 通过 |
| 窗边猫咪 | 35 | 54.92 秒 | 71.59 秒 | 通过 |
| 街边咖啡馆 | 69 | 48.82 秒 | 128.92 秒 | 通过 |
| 注册流程图 | 32 | 18.11 秒 | 43.34 秒 | 通过 |

每个题目检查用户要求的部位及原生几何信息；流程图额外检查文字位于节点内部，以及箭头数量。五份原生文档、报告及 PNG 导出保存在 `runs/quality-real-1/`。这些结果证明指定样本可以执行并形成相应对象，画面质量仍需要根据每题的人工标准评价。

```bash
python -m scripts.evaluate_quality --url http://127.0.0.1:8001 --output-dir runs/drawing-quality
```

## 逐步作画耗时

回放使用已经保存的真实模型动作，经过当前生产绘图执行器逐项提交，不发送模型请求。相同的 113 个对象及几何信息保持一致，112 条画笔轨迹全部具有有序的过程事件，撤销和重做检查通过。

| 指标 | 原始记录 | 当前回放 |
| --- | --- | --- |
| 画笔过程累计用时 | 97.52 秒 | 42.61 秒 |
| 画笔过程事件 | 1,238 次 | 789 次 |

当前整份动作回放用时 44.33 秒。画笔按路径长度安排展示时长，短笔画不再使用统一的较长等待。该测量只比较同一作品的绘图过程，不能代表模型生成总耗时的改善。记录为 `runs/playback-real-3.json`。

```bash
python -m scripts.evaluate_playback --source-database runs/portrait-optimization-20261019.sqlite3 --task-id cf26f4c02bef4b1b8c8d35c53a85114c --database runs/playback-check.sqlite3 --output runs/playback-check.json
```

## 免费公共检索

检索使用 DDGS 公共后端和实际网页正文。一次两题检查中，山水方法检索用时 10.28 秒，返回三个来源；手持杯子的方法检索用时 11.98 秒，返回两个来源。相关正文、同画布缓存、资料归属和私有地址拒绝检查通过，记录为 `runs/research-real-4.json`。

其他实际运行也出现过空结果、超时和 TLS 错误。公共搜索的可用性不能由上述两题的成功记录推算；工具会返回实际错误，Agent 据此调整查询或说明资料不足。

完整研究绘图检查让主 Agent 创建研究子 Agent，搜索并阅读鸟翼、鸟足的具体画法，然后使用原生画笔完成作品。最近一次产生 51 个对象，用时 325.31 秒；完成状态、子 Agent 搜索、正文阅读、真实来源、原生图形、消息来源链接、工具错误返回所属上下文等 7 项检查全部通过。过程中两次工具错误均保留并返回，没有省略。记录为 `runs/research-agent-real-2.json`，完整日志保存在同名 SQLite 数据库。

```bash
python -m scripts.evaluate_research --database runs/research-check.sqlite3 --output runs/research-check.json
python -m scripts.evaluate_research_agent --database runs/research-agent-check.sqlite3 --output runs/research-agent-check.json
```

## 子 Agent 生命周期与请求恢复

真实 Ark 生命周期检查 15/15 项通过，用时 23.08 秒，包含两名子 Agent 的并发请求、工具检查点后的关闭和恢复、连续输入、编号保留、重复提交和画布隔离。模型请求没有执行期限，用户仍可以停止。

OpenAI 兼容 Chat Completions 路由使用官方异步 SDK。该次检查未出现未等待协程提示。记录为 `runs/agent-lifecycle-optimized-1.json`，对应 [Issue #42](https://github.com/bugkeep/ai-draw/issues/42)。其他厂商路由仍需要分别进行真实连接检查。

同一模型连接发生 HTTP 429 时，主 Agent 与子 Agent 共用恢复队列。最近的蒙娜丽莎样本在三次重试后完成，说明该次请求可以恢复；7 分 33 秒的整体耗时仍未达到低延迟目标。

```bash
python -m scripts.evaluate_agent_lifecycle --output runs/agent-lifecycle-check.json
```

## 数据与结果解释

所有模型评测都需要实际可用的模型连接；自动化检查使用实际 SQLite 与原生对象，不预设模型返回。作品、录像和完整网页日志可能包含用户内容，默认不加入 Git。命令中的历史数据库及任务编号用于复查本机已有记录，新环境应运行生成命令建立自己的记录。

工具执行成功、对象结构完整、延迟和画面质量分别评价。目前可复现检查覆盖执行流程和指定样本，稳定的低延迟及高质量成图仍需要更多实际样本验证。

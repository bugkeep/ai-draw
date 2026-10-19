# 绘图工具与资料检索

## 原生画笔与连续编辑

`draw_paths` 接收有名称的画笔关键点，支持共享 `origin`、`scale`、颜色、线宽和透明度。每条路径生成一个原生 `freedraw` 对象。平滑曲线经过给定关键点，`smooth=false` 保留折线，`closed=true` 连接首尾。一次调用会先校验全部路径，再准备动作；共享画布仍由主 Agent 接受并逐项提交。

人像、动物、山水等曲线题材使用画笔；建筑边缘和流程连线可以使用原生直线及箭头。所有对象保留独立编号和可搜索名称，后续对话可以选择、移动、改变样式或修改曲线。

`inspect_scene` 默认返回对象概况。需要修改已有轮廓时，可按名称或编号查询并设置 `include_geometry=true`，获取相对对象原点的路径点列。`reshape_path` 接收绝对关键点，替换指定路径的几何信息，保留对象编号、名称、样式和其他图形。修改经过已有内容检查与历史记录，支持撤销和重做。

复杂作品由主 Agent 提供统一坐标、比例和部位连接位置。子 Agent 在自己的画布准备独立部分，返回简短说明和 `result_id`；完整工具记录及点列保存在 SQLite。主 Agent 审核结果并按顺序提交，也负责检查用户要求的主体、道具、背景和必要文字。

## 免费公共资料检索

主 Agent 和子 Agent 均具有以下工具：

| 工具 | 输入 | 返回 |
| --- | --- | --- |
| `search_references` | 具体 `query`、主题词 `subject_terms`、绘图问题 `goal`、可选 `page` | 已读取来源的 `reference_id`、标题、链接、相关片段与筛选数量 |
| `read_reference` | 当前画布的 `reference_id`、关注词 `focus_terms` | 同一来源中符合关注词的片段 |

检索使用 DDGS 的公共搜索接口，无需搜索服务 API Key。默认后端为 `yahoo,duckduckgo,google,startpage`，可在服务启动前通过 `DRAW_AGENT_SEARCH_BACKEND` 配置 DDGS 支持的后端名称。后端可用性受公共服务、网络和频率保护影响；连接错误与没有可用资料均会明确返回工具结果，Agent 可以根据错误调整检索词或说明资料不足。

筛选过程包括主题词匹配、文章正文读取、绘画步骤判定、重复内容检查及来源数量控制。来源需要包含相应题材以及轮廓、比例、透视、阴影等绘画内容，并提供具体操作步骤。返回片段优先选择步骤标题、绘画动作及相关部位，检索和阅读均不会把完整网页加入主 Agent 的上下文。

检索可读取每页最多八个搜索结果，并发检查排名靠前的五个网页，返回最多三个来源和每个来源最多三段相关文字。这些数量用于控制资料阅读量；绘图动作和模型任务继续按用户目标执行。网页读取具有网络等待、正文长度和重定向保护，避免资料工具长期占用连接。

检索结果、网页正文、筛选原因和短返回分别保存到现有 `tool_logs`。成功检索在同一画布缓存一小时，参考编号与画布关联，其他画布不能读取。网页只允许公开 HTTP/HTTPS 地址，每次跳转均检查目标地址；本地和私有网络地址会被拒绝。网页内容作为参考数据处理，不能改变工具权限或要求访问凭据。

涉及较多资料时，主 Agent 可以创建研究子 Agent。研究子 Agent 阅读来源后返回作画方案和来源编号，主 Agent 据此安排画笔。子 Agent 使用相同模型连接，保持独立上下文，并且不能创建或控制其他 Agent。

成功调用 `read_reference` 后，主 Agent 上下文会获得本任务最多三个来源的编号、标题和链接。完成消息展示相同来源的可点击链接，刷新后仍从 SQLite 的阅读记录恢复。链接由已经成功读取的工具结果提供，原始网页和工具过程继续保存在数据库。

当前筛选使用正文内容和规则评分。它能减少无关介绍、重复网页及没有步骤的页面，不能保证每篇教程的专业水平。原始链接保留在工具结果中，便于检查来源。

## 作品导出

“导出作品”读取当前编辑器快照，导出期间使用该快照的图形、文字、背景与图片文件。

| 格式 | 选项与用途 |
| --- | --- |
| PNG | 1、2、4 倍清晰度；可选择透明背景 |
| JPG | 1、2、4 倍清晰度；使用画布背景 |
| WebP | 1、2、4 倍清晰度；可选择透明背景 |
| SVG | 原生矢量导出；可选择透明背景 |
| Excalidraw | 保存原生文档，便于继续编辑 |

图片由 Excalidraw 导出器生成，并检查浏览器返回的实际 MIME 类型。SVG 和 Excalidraw 分别使用原生矢量及文档导出器。浏览器不支持某种格式时显示错误，保留导出窗口供用户选择其他格式。文件名称来自当前画布名称，可以修改，并处理文件系统不支持的字符。

## 可复现检查

```bash
python -m scripts.evaluate_research
python -m scripts.evaluate_quality
python -m scripts.evaluate_research_agent
cd frontend-app
node scripts/export_smoke.js
```

检索检查使用真实公共搜索。绘图与 Agent 检查需要已配置模型的运行服务，可通过 `--url` 指定地址；浏览器检查使用 `DRAW_AGENT_URL`。数据与作品写入 Git 忽略的 `runs/`。批量题目和人工质量标准保存在 `evaluations/drawing-quality.json`；对象、延迟与画面质量分别记录。

`evaluate_research_agent --database runs/research-agent.sqlite3` 可以在独立进程内加载生产 ASGI 应用，使用环境变量中的真实模型配置检查同样的 HTTP API 与 Agent 流程，无需额外监听端口。

`python -m scripts.verify_browser --database runs/browser-verification.sqlite3` 自动在空闲本地端口运行生产服务，依次执行浏览器检查，然后正常结束服务。`--scripts` 可选择检查脚本。已有真实研究任务时，设置 `DRAW_AGENT_SESSION` 后，可使用 `--database DATABASE --scripts references_smoke.js` 验证来源链接、刷新恢复及画布归属。

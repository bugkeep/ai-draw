# 绘图工具与资料检索

## 原生画笔与连续编辑

`draw_paths` 接收有名称的画笔关键点，支持共享 `origin`、`scale`、颜色、线宽和透明度。每条路径生成一个原生 `freedraw` 对象。平滑曲线经过给定关键点，`smooth=false` 保留折线，`closed=true` 连接首尾。一次调用会先校验全部路径，再准备动作；共享画布仍由主 Agent 接受并逐项提交。

人像、动物、山水等曲线题材使用画笔；建筑边缘和流程连线可以使用原生直线及箭头。所有对象保留独立编号和可搜索名称，后续对话可以选择、移动、改变样式或修改曲线。

`inspect_scene` 默认返回最近对象与当前选择的概况。可以按名称、编号或 `region={x,y,width,height}` 查询全部对象，组合条件同时生效；区域查询包含较早创建和经过旋转的对象。设置 `include_geometry=true` 可以获取相对对象原点的路径点列，旋转角度 `angle` 以弧度表示，围绕对象中心旋转。`reshape_path` 接收最终画布中的绝对关键点，替换指定路径的几何信息，保留对象编号、名称、样式和其他图形。修改经过已有内容检查与历史记录，支持撤销和重做。

`draw_paths` 的 `strokeWidth` 使用 Excalidraw 原生画笔单位，渲染基准尺寸为 `strokeWidth × 4.25`，默认值为 `0.4`。素描从较轻的参考线开始，统一比例与部位连接位置，轮廓清楚后添加适量阴影，并移除重复参考线。

闭合笔画支持共享及逐路径的 `backgroundColor`，默认透明。例如，黑白线稿的人脸或动物身体可以使用 `closed=true`、`backgroundColor=#ffffff`，让前景遮住其后的窗框、山线或树枝；细节线仍保持透明。填色仍是同一个可选择和修改的原生画笔对象。填色路径必须闭合；`reshape_path` 未指定 `closed` 时保留已有填色轮廓的闭合状态，明确设置 `closed=false` 才打开轮廓并移除填色。

`reorder_elements` 接收对象 `ids` 和 `position=front|back`，把目标移至最前或最后，保持所选对象之间以及其他对象之间的相对顺序。它不改变几何、编号和样式；锁定对象不会作为移动目标。一次调用对应一步撤销，顺序变化随画布增量事件发送。主 Agent 可以直接调整层级，子 Agent 也可以在私有草稿中准备层级调整。

未指定 `ids` 的橡皮擦会保留完全不透明、实心前景填色所遮盖的背景部分。局部线段擦除保留填色轮廓本身；整条擦除可用 `mode=stroke`。显式传入 `ids` 时可以有意修改被遮住的目标；同时设置 `visible_only=true` 可保留前景保护。透明、半透明及非实心填色不作为完全遮挡区域。准备动作后会保存这个语义，提交和撤销不会把可见区域擦除悄悄变成穿透擦除。

复杂作品由主 Agent 提供统一坐标、比例和部位连接位置。子 Agent 在自己的画布准备独立部分，返回简短说明和 `result_id`；完整工具记录及点列保存在 SQLite。主 Agent 审核结果并按顺序提交，也负责检查用户要求的主体、道具、背景和必要文字。

## 图片检查与橡皮擦

每次模型请求前，runtime 根据当前对象内容准备实际 PNG：主 Agent 看到共享画布，子 Agent 看到自己的私有草稿。图片通过与文字相同的模型连接发送，包含图片像素与画布坐标之间的映射。模型需要同时支持图片输入和 function calling。

`view_canvas` 可以请求当前全图，或者通过 `region` 放大面部、双手等局部。图片由相同版本的 Excalidraw `exportToCanvas` 生成，最长边为 1280 像素，局部检查不会移动或修改作品。`inspect_scene` 使用相同区域查询对象编号，再由 `reshape_path`、`erase_strokes` 或其他绘图工具修正。

`erase_strokes` 接收绝对坐标 `points=[[x,y],...]` 和画布单位的 `radius`。即使只擦除一个位置，仍需要使用包含一个点的数组。`mode=segment` 擦除画笔或线条中触及的部分，保留两端的原生对象、名称、样式和压力数据；`mode=stroke` 删除触及的完整对象。`ids` 可限定目标，锁定对象保留。每次手势生成一个绘图动作，支持一次撤销及重做。

子 Agent 准备的擦除动作记录当时实际触及的对象编号和内容指纹。后续添加的其他笔画不会因为处在同一区域而受到该动作影响；已变化的对象通过提交检查返回冲突。子 Agent 擦除已分配参考线后，其上下文记录对象已经移除，可以继续重画。

SQLite 的 `canvas_observations` 保存 PNG、画布内容标识、所属会话、任务、Agent、执行编号和已发送状态。相同调用者的相同作品与区域复用图片；模型上下文只附当前全图与最近需要的局部图片，历史 PNG 和完整工具记录不会持续加入对话。日志只记录图片编号与元信息。完成前必须已向相应 Agent 提供当前全图；图片之后发生内容修改时，需要再次检查，工具错误始终返回原 `call_id` 的上下文。

## 工作记忆与历史检索

主 Agent 与子 Agent 均提供 `search_memory`、`recall_memory` 和 `compact_context`。搜索返回当前对象信息与历史记录编号，精确读取返回当前原生属性、分页路径点或已保存的完整调用原文。工作记忆保存目标、约束、重要对象、已完成及待办事项，采用版本校验并保留历史说明。成功的长工具结果只提供摘要，未读工具错误完整返回；其他 Agent、画布与后续排队指令的记录保持隔离。参数、分页和恢复行为见[Agent 记忆与工具压缩](agent-memory.md)。

## 免费公共资料检索

主 Agent 和子 Agent 均具有以下工具：

| 工具 | 输入 | 返回 |
| --- | --- | --- |
| `search_references` | 具体 `query`、主题词 `subject_terms`、绘图问题 `goal`、可选 `page` | 已读取来源的 `reference_id`、标题、链接、相关片段与筛选数量 |
| `read_reference` | 当前画布的 `reference_id`、关注词 `focus_terms` | 同一来源中符合关注词的片段 |

检索使用 DDGS 的公共搜索接口，无需搜索服务 API Key。默认后端为 `yahoo,duckduckgo,google,startpage`，可在服务启动前通过 `DRAW_AGENT_SEARCH_BACKEND` 配置 DDGS 支持的后端名称。后端可用性受公共服务、网络和频率保护影响；连接错误与没有可用资料均会明确返回工具结果，Agent 可以根据错误调整检索词或说明资料不足。

筛选过程包括主题词匹配、文章正文读取、绘画步骤判定、重复内容检查及来源数量控制。来源需要包含相应题材以及轮廓、比例、透视、阴影等绘画内容，并提供具体操作步骤。返回片段优先选择步骤标题、绘画动作及相关部位，检索和阅读均不会把完整网页加入主 Agent 的上下文。

检索可读取每页最多八个搜索结果，并发检查排名靠前的五个网页，返回最多三个来源和每个来源最多三段相关文字。这些数量用于控制资料阅读量；绘图动作和模型任务继续按用户目标执行。网页读取具有网络等待、正文长度和重定向保护，避免资料工具长期占用连接。

检索结果、网页正文、筛选原因和短返回分别保存到现有 `tool_logs`。成功检索在同一画布缓存一小时，参考编号与画布关联，其他画布和后续任务的记录不能读取。网页只允许公开 HTTP/HTTPS 地址，每次跳转均检查目标地址；本地和私有网络地址会被拒绝。网页内容作为参考数据处理，不能改变工具权限或要求访问凭据。

涉及较多资料时，主 Agent 可以创建研究子 Agent。研究子 Agent 阅读来源后返回作画方案和来源编号，主 Agent 据此安排画笔。子 Agent 使用相同模型连接，保持独立上下文，并且不能创建或控制其他 Agent。

成功调用 `read_reference` 后，主 Agent 上下文会获得本任务最多三个来源的编号、标题和链接。完成消息展示相同来源的可点击链接，刷新后仍从 SQLite 的阅读记录恢复。链接由已经成功读取的工具结果提供，原始网页和工具过程继续保存在数据库。

当前筛选使用正文内容和规则评分。它能减少无关介绍、重复网页及没有步骤的页面，不能保证每篇教程的专业水平。原始链接保留在工具结果中，便于检查来源。

## 参考图片与区域轮廓

| 工具 | 输入 | 返回与行为 |
| --- | --- | --- |
| `search_reference_images` | `query`、`subject_terms`、`goal`、`source`、`page` | 实际读取的图片编号、来源信息与候选对比预览 |
| `view_reference_image` | `reference_id`、可选像素 `region` | 将完整参考图及最近所需局部图附加到该 Agent 的下一轮上下文 |
| `trace_reference` | 图片编号、区域名称、共同 `source_frame`、源像素 `region`、画布 `target`、提取与样式参数、第一页数量 | 创建固定计划，准备原生 `freedraw` 对象，返回 `trace_plan_id`、对象编号、映射和 `next_page` |
| `continue_reference_trace` | `trace_plan_id`、`offset`、`limit`，可直接传入上一页的 `next_page` | 使用已保存计划的原参数、原区域、原映射和样式准备后续路径 |

`source=pages` 使用公共文字搜索定位插图页面，通过 trafilatura 与 lxml 读取页面元信息和与主体相关的图片地址。`source=images` 使用公共图片索引，解析由 DDGS 提供。来源页面、图片和图片索引通过 HTTPX 读取，逐次检查跳转目标，下载后通过 Pillow 解码实际像素，检查分辨率并合并重复来源及相似图片。搜索使用主题与查询相关性排序，最多提供三个不同来源；图片元信息不能证明姿态与用户要求相符，Agent 需要查看实际图片后选择。

`source=commons` 直接查询 Wikimedia Commons 的公开图片目录，无需付费搜索密钥，适合查找公开作品、版画及自然史插图。它通过 [MediaWiki Imageinfo API](https://www.mediawiki.org/wiki/API:Imageinfo) 取得文件页、图片地址、作者和来源声明的许可信息；文档、音频和视频结果被排除。图片仍经过相同的公开地址、下载、像素解码、重复筛选、SQLite 归属及完整图片检查流程。目录中也会出现不同姿态或相近题材，标题匹配不表示图像满足本次作画要求；网页搜索不适合时，Agent 可以切换来源或调整查询。

目录提供的缩略图不可用时，可尝试同一条目录记录的原图地址。两次读取都经过公开地址、跳转、文件与解码尺寸检查；成功时保留原图实际地址和失败缩略图记录。筛选日志也保存所有实际解码候选的尺寸及未成功来源，便于区分网络问题与图片内容不合适。

参考图片只允许公开 HTTP/HTTPS 来源，网络读取具有 20 MB 文件、2,400 万解码像素和跳转保护。支持 Pillow 的 PNG、JPEG、WebP、GIF、BMP、TIFF、AVIF 和 ICO 解码；动画图片使用第一帧，EXIF 方向校正后将透明区域铺成白色。保存的参考图最长边为 2048 像素，查看图片最长边为 1280 像素。工具返回的 `width`、`height` 与区域坐标均使用保存后的参考图尺寸。

`reference_images` 保存原始文件、解码图片、来源及所属会话和任务；`reference_image_views` 保存各调用者的当前图片、区域与实际发送状态；`reference_search_previews` 保存带 A/B/C 标签的候选对比图及调用者归属；`reference_traces` 保存提取参数与完整源像素路径；`reference_trace_plans` 保存固定的区域、映射和样式，供主 Agent 与子 Agent 使用同一计划继续分页。搜索成功后，候选预览直接进入对应 Agent 的下一轮图片上下文。主 Agent 首次使用已有资料时也会收到候选对比图，可直接选择已保存的参考图。

主 Agent 与子 Agent 分别选择图片，每次模型响应成功后才记录已发送。候选预览帮助选择图片，提取前仍需检查本调用者已收到选定来源的完整图片；预览和其他 Agent 的查看记录不能代替该检查。选中完整参考图后，对比预览从当前上下文移除。当前上下文保留选定参考图及所需局部图片，历史图片继续保存在数据库。

`mode=edges` 使用 scikit-image 的 Canny 提取照片或绘画的边界；`mode=ink` 对浅色纸面上的深色线条进行阈值处理，再生成中心路径。`contrast=local` 使用 CLAHE 增强低对比度图片中的结构，默认 `contrast=none` 保留原图对比度。骨架化后由 Skan 读取有序分支，通过 Shapely 简化曲线和裁切区域。`sigma` 控制图像平滑，`min_length` 过滤短路径，`simplify` 指定源像素误差；这些参数需要结合当前参考图选择，提取后仍需检查画布上的主体结构与线条。

`source_frame={x,y,width,height}` 指定原图中完整作品的共同裁剪范围，省略时使用整张原图。`target={x,y,width,height}` 表示该完整范围在画布中的位置，按比例居中映射。`region` 使用原图像素坐标，只选择共同范围内的某个部位，不改变比例和位置；分别绘制面部、双手和背景的子 Agent 使用相同 `source_frame` 和 `target`。超出共同范围的部位请求会返回参数错误。

`trace_reference` 创建计划并准备第一页，返回固定的 `trace_plan_id` 和下一页请求 `next_page`。后续使用 `continue_reference_trace`，从 SQLite 读取该计划的原参数；修改区域、映射或提取参数时创建新计划，从其第一条路径开始。主 Agent 可以把计划编号及下一页请求交给子 Agent，子 Agent 查看完整来源后按原计划继续。路径可以持续分页，没有作品路径总数限制。

压缩记忆保留各提取配置的历史路径范围、重复生成数量、首个缺失位置及相关 `memory_ref`。相同配置改用新的名称，仍记为相同来源路径。该记录帮助 Agent 查询原对象并修改，已经擦除的历史路径不会据此自动恢复；历史范围本身不表示当前画面已经完成。

例如，以“右手”为区域名称生成的路径使用“右手/轮廓-0001”等名称，每条都有独立编号。后续通过 `search_memory` 找到对应笔画，再用原生选择、移动、`reshape_path` 或 `erase_strokes` 编辑；撤销和重做继续使用同一历史流程。子 Agent 提取的笔画保存在私有草稿，主 Agent 接收摘要后决定提交。完整点列保存在 SQLite，工具对话返回编号和统计。画布与导出的 Excalidraw 文档不需要参考图片对象。

来源页面、说明和图片中的文字均属于外部参考数据。来源许可未核实时记录为 `unverified`，不根据搜索标题推断许可。公共搜索或来源被拒绝时返回实际错误及必要筛选说明，Agent 可以调整查询；来源可访问性和整幅作品质量需要通过实际生成分别验收。

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
python -m scripts.evaluate_reference_images --database runs/reference-images.sqlite3 --output runs/reference-images.json
cd frontend-app
node scripts/export_smoke.js
```

检索检查使用真实公共搜索。绘图与 Agent 检查需要已配置模型的运行服务，可通过 `--url` 指定地址；浏览器检查使用 `DRAW_AGENT_URL`。数据与作品写入 Git 忽略的 `runs/`。批量题目和人工质量标准保存在 `evaluations/drawing-quality.json`；对象、延迟与画面质量分别记录。

`evaluate_research_agent --database runs/research-agent.sqlite3` 可以在独立进程内加载生产 ASGI 应用，使用环境变量中的真实模型配置检查同样的 HTTP API 与 Agent 流程，无需额外监听端口。

`evaluate_quality --database runs/quality.sqlite3 --output-dir runs/quality` 可以使用独立数据库在进程内运行同样的生产 API，并保存各题的原生作品及实际检查 PNG。`evaluate_visual_edit` 检查看图后擦除多余眼睛、局部擦除和连续编辑。`evaluate_visual_quality` 根据实际 PNG 或原生文档生成有证据的模型辅助评价，保留独立于结构检查的结果。

`evaluate_reference_images` 使用真实公共来源检查下载、图片上下文、缓存及画布隔离，并使用实际模型逐张评价主体、姿态和必要结构是否可见。评测需要已配置的模型和新的独立数据库。`--recheck REPORT.json` 可以使用对应旧评测数据库中的实际图片重新评价，不发起新的网页搜索；该结果只说明保存图片的可用程度。

`evaluate_reference_edit --source-database DATABASE --reference-id IMAGE_ID --database runs/reference-edit.sqlite3 --output runs/reference-edit.json` 使用实际已经下载的参考图和真实模型，检查子 Agent 提取、主 Agent 提交、复用同一子 Agent、原编号连续编辑、其他对象保护及撤销重做。该检查验证参考轮廓执行流程，整幅作品的视觉验收仍需单独进行。

`evaluate_trace_continuation --source-database DATABASE --reference-id IMAGE_ID --database runs/trace-continuation.sqlite3 --output runs/trace-continuation.json` 使用真实模型验证主 Agent 创建计划、子 Agent 使用同一计划继续、参数和映射保持、已提交对象保护及实际图片反馈。

`prepare_reference_evaluation --source-database DATABASE --reference-id IMAGE_ID --database runs/reference-drawing.sqlite3 --output runs/reference-seed.json` 在新的独立画布中导入实际保存的参考图，`--reference-id` 可重复。将输出的 `session_id` 设置为 `DRAW_AGENT_REFERENCE_SESSION`，再用 `verify_browser --database runs/reference-drawing.sqlite3 --scripts record_portrait.js` 录制该画布上的完整绘图。该流程验证已有参考图的使用，不衡量新一次公共搜索的可用性。

`python -m scripts.verify_browser --database runs/browser-verification.sqlite3` 自动在空闲本地端口运行生产服务，依次执行浏览器检查，然后正常结束服务。`--scripts` 可选择检查脚本。已有真实研究任务时，设置 `DRAW_AGENT_SESSION` 后，可使用 `--database DATABASE --scripts references_smoke.js` 验证来源链接、刷新恢复及画布归属。

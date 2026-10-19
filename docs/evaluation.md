# 真实操作评测

评测日期：2026 年 10 月 19 日至 20 日。环境为 Windows、Python 3.13、Node.js 24、Chrome，实际模型为 Ark Agent Plan `ark-code-latest`。服务、SQLite 和浏览器均在本机运行。当前 API 6 包含实际画布图片检查、橡皮擦、候选参考图预览、区域轮廓与固定计划分页。完整工具过程及作品保存在本机 Git 忽略的 `runs/`；下文人物与多题材生成表保留 API 4 样本的版本背景，画面验收单独记录。

## 自动化与浏览器检查

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| 后端检查 | 92/92 通过，27.94 秒 | 实际 SQLite、原生渲染、全图与局部图片、参考图预览与裁剪、固定计划分页、低对比度轮廓、归属与任务顺序、擦除、旋转几何、路径修改、记忆检索与压缩、重复提取范围摘要、历史事务、完整错误、输入顺序、保存恢复、提交幂等、共享恢复队列 |
| 浏览器完整检查 | 45/45 通过 | 编辑器 5 项、导出 13 项、设置 8 项、滚动与名称 9 项、删除 10 项 |
| 运行状态与停止 | 8/8 通过 | 实际模型请求、用时更新、刷新与切换恢复、其他画布隔离、停止、重复停止及终止状态清理 |
| 已读取来源展示 | 4/4 通过 | 实际研究任务的来源链接、刷新恢复及其他画布的资料归属 |
| 前端构建与依赖审计 | 通过 | `npm run build`；生产依赖审计未报告漏洞 |

浏览器导出检查实际下载 PNG、JPG、WebP、SVG 和 Excalidraw 文件，解析文件内容，并检查图片尺寸、透明背景、名称和小尺寸窗口。PNG 另外检查 2 倍与 4 倍分辨率。独立浏览器检查另外启用 `DRAW_AGENT_TEST_MODEL=1`，实际点击设置页的连接测试，图片识别、工具及文本响应通过。

后端检查保留 Starlette/AnyIO 和 scikit-image/NumPy 的弃用提示。前端构建保留打包体积提示，相关信息均可在检查输出中查看。

```bash
python -m pytest -q
python -m scripts.verify_browser --database runs/browser-verification.sqlite3
python -m scripts.verify_browser --database runs/activity-verification.sqlite3 --scripts activity_smoke.js
```

浏览器脚本使用生产应用、独立数据库和系统分配的本地端口，检查结束后正常关闭测试服务。`activity_smoke.js` 需要环境变量中配置的真实模型连接。

## 看图修正与检查隔离

API 5 使用当前模型和实际原生图形进行两轮连续修正。初始面部的对象名称均为普通编号，模型需要根据 PNG 找到多余眼睛；随后在相同画布添加穿过面部的横线，要求只擦除面部范围内的线段。

| 操作 | 用时 | 检查结果 |
| --- | --- | --- |
| 查看人脸，擦除多余眼睛及瞳孔 | 12.81 秒 | 正常双眼、鼻子、嘴与脸部轮廓保留 |
| 局部擦除穿过面部的横线 | 31.05 秒 | 左右两端保留，其他对象保持原样 |
| 模型连接测试 | 5.32 秒 | 随机图形图片识别、工具调用和后续文本均通过 |

8001 用户服务已加载 API 5，重启时核对原有四个会话及全部关联表内容一致。该地址的实际图片、工具和文本连接测试通过，用时 6.78 秒。

两轮修正共 11 项检查通过，包含实际图片进入模型上下文、橡皮擦使用、对象保护、局部几何、撤销与重做。过程中一次坐标格式错误完整返回模型上下文，模型修正参数后完成。记录为 `runs/visual-edit-real-1.json`、`runs/visual-edit-real-1.sqlite3`、四张 `runs/visual-edit-real-1-view-*.png` 和 `runs/visual-model-connection.json`。

真实子 Agent 检查为 18/18 项通过，用时 27.66 秒，包含擦除已分配参考线后继续执行、检查自己的当前草稿、保留其他图形、并发请求、连续输入、关闭恢复、原编号编辑与画布隔离。记录为 `runs/agent-lifecycle-vision-final.json`。这两类检查验证执行和反馈能力，不能单独证明复杂作品已达到用户要求的艺术质量。

```bash
python -m scripts.evaluate_visual_edit --database runs/visual-edit.sqlite3 --output runs/visual-edit.json
python -m scripts.evaluate_agent_lifecycle --database runs/agent-lifecycle.sqlite3 --output runs/agent-lifecycle.json
python -m scripts.evaluate_visual_quality runs/drawing-quality/cat-on-window.excalidraw --prompt "画一只蜷在窗台上睡觉的猫" --output runs/cat-visual-review.json
```

`evaluate_visual_quality` 对 PNG 或实际原生作品先进行不含题目与对象名称的图片描述，再给出各项画面标准及证据。评价模型使用已配置连接，原始响应和导出图片保存在 `runs/`。模型辅助评价仍需要与实际作品和用户验收结合。

## 人物素描与连续编辑

以下生成记录使用 API 4。浏览器只发送“画一幅蒙娜丽莎素描图”，录制完整操作并通过导出窗口下载作品。该次得到 130 个原生 Excalidraw 对象，脸部、双眼、鼻子、嘴唇、头发、双手、衣服和背景的 11 项结构检查全部通过；图形中没有图片对象。

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

以下生成记录使用 API 4。题目及人工画面标准保存在 `evaluations/drawing-quality.json`。五个题目均使用真实模型，分别生成独立画布，工具错误及模型重试均为零。

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

## 参考图片与原生轮廓连续编辑

参考图片检查使用实际 Pillow 解码、SQLite 和原生绘图工具，16 项通过，覆盖 PNG/JPEG/WebP/GIF/BMP/TIFF、EXIF 方向、透明区域、错误文件、插图页面解析、画布与任务顺序隔离、候选预览与完整图片检查、重启、裁剪后的共同映射、固定计划分页、局部对比度、移动、擦除、撤销重做与重复调用。

真实模型检查从已经实际下载的蒙娜丽莎参考图提取三条原生轮廓，由子 Agent 在私有草稿中准备，主 Agent 审阅后提交；随后通过 `send_input` 复用同一子 Agent，检索与读取原笔画，向右移动 20 像素。全部 12 项检查通过，第一轮 39.19 秒，第二轮 51.80 秒，工具错误为零。检查包含参考图片实际进入子 Agent 上下文、主 Agent 提交两次结果、原编号保持、其他三个对象保护、实际画布图片反馈和撤销重做。记录为 `runs/reference-edit-model-final.json` 及同名数据库。

另一组真实模型两轮操作同样完成，分别为 51.43 秒与 48.50 秒；一次无效工具参数完整进入下一轮上下文，随后修正。原始过程保存在 `runs/reference-edit-model-verified.sqlite3`，用于核查错误反馈与恢复。

公共检索分别检查人物、山水与动物。图片索引能返回并解码三类图片，但部分结果只匹配主体名称，姿态和绘画形式需要实际看图判断。插图来源检索读取到人物图和山体绘画教程；其他请求仍出现空结果、403 或网络错误。报告为 `runs/reference-images-market-verified.json`、`runs/reference-articles-verified.json` 和 `runs/reference-tutorials-verified.json`，后两组未通过全部来源可用性检查。

实际模型重新评价已保存的九张参考图片，六张能够支持对应结构问题。人物组有两张可用图片，一张为宣传封面；动物组三张图片中只有一张能支持坐姿身体结构，另两张无法提供所需完整姿态。三个题目均至少有一张可用来源。报告为 `runs/reference-usefulness-20261019.json`，该次使用已有图片，没有重新衡量公共搜索的连接可用性。评测现已分别记录下载与隔离检查、实际图片可用性及整幅作品质量。

固定计划的真实模型检查为 11/11 项通过，用时 54.62 秒，工具错误为零。主 Agent 查看完整参考图并提取前两条路径，把 `trace_plan_id` 和 `next_page` 交给子 Agent；子 Agent 查看完整来源后使用 `continue_reference_trace` 准备后两条，最后由主 Agent 提交。检查核对计划编号、区域、参数对应的提取编号、画布映射、连续编号、原笔画保护、其他图形保护、实际图片上下文和错误反馈。记录为 `runs/trace-plan-model-final-20261020.json` 及同名数据库。该检查验证分页执行，不作为整幅人物画的艺术质量结论。

压缩记忆从实际人物任务的 22 次成功提取记录中计算出：共生成 1,247 条不同的来源路径，另有 2,180 条重复路径；新参数对应序列的开头 160 条始终没有生成。该记录直接说明了跨参数沿用页码及更名重复提取的问题。固定计划保留区域与参数，范围摘要在压缩后继续提供历史区间和首个缺失位置。分析报告为 `runs/trace-coverage-audit-20261020.json`，仅重放已有记录，没有将该人物任务记为成功。

“画一幅蒙娜丽莎素描图”的独立浏览器运行没有通过整幅作品验收。两次运行在未取得合适参考图片后继续绘制，已通过本次评测的停止接口终止并保存实际录像与图片，记录为 `runs/portrait-reference-verified.json` 与 `runs/portrait-reference-articles.json`。原生轮廓与连续编辑通过不代表人物、山水等整幅作品已经达到目标质量；GitHub Issue #44 继续跟踪来源可用性、构图质量与完整任务延迟。

```bash
python -m scripts.evaluate_reference_images --database runs/reference-images.sqlite3 --output runs/reference-images.json
python -m scripts.evaluate_reference_edit --source-database SOURCE_DATABASE --reference-id IMAGE_ID --database runs/reference-edit.sqlite3 --output runs/reference-edit.json
python -m scripts.evaluate_trace_continuation --source-database SOURCE_DATABASE --reference-id IMAGE_ID --database runs/trace-continuation.sqlite3 --output runs/trace-continuation.json
```

## 连续修改与历史保存耗时

同一份真实人物作品包含 993 个对象，画布 JSON 为 925,726 字节，原有撤销历史为 45,286,529 字节。在同一份只读快照生成的两个独立数据库副本中，分别进行六次连续移动、六次撤销和六次重做，记录中位耗时：

| 操作 | SQLite JSON 历史 | 逐条保存历史 |
| --- | --- | --- |
| 移动对象 | 1,429.76 毫秒 | 207.00 毫秒 |
| 撤销 | 1,189.38 毫秒 | 106.56 毫秒 |
| 重做 | 999.00 毫秒 | 101.41 毫秒 |

移动操作在该样本减少约 85.5%。每一步历史独立保存在 SQLite 的 `document_history` 表，保留现有 50 步撤销和重做。每次修改只增加当前快照并删除超出的旧步骤；画布修改、历史更新和子 Agent 提交进度在同一事务中保存。两个连接同时编辑时，先取得写入事务，再读取对应画布及历史。

已有 JSON 历史在启动时自动迁移，旧数组与新记录在同一事务中更新。迁移保留已有快照顺序，随后编辑或撤销时继续按现有规则保留最近 50 步；重复启动不会重复导入。迁移失败时原有数组和全部历史记录保持原样。升级时应停止使用同一数据库的旧版服务，再启动新版。

两次评测均通过全部历史快照及顺序、撤销恢复、重做恢复和其他文档保护检查。十项历史检查还覆盖重启恢复、选择及元数据变化保留重做、迁移失败恢复、提交回执或画布保存失败恢复、原有历史内容保护、文档删除清理、两个连接同时修改，以及检测到新旧服务混合写入时保留历史并报告错误。

记录为 `runs/history-trace-before-20261020.json`、`runs/history-trace-after-20261020.json` 及对应数据库副本。报告包含 SQLite 版本、存储代码标识、各次实际耗时和 cProfile 结果。逐条历史中的快照总量约为 45 MB，历史内容没有因存储优化被删除。该测量包含本地存储与画布事件保存，不发送模型请求，不能代表整幅图的生成时间。

```bash
python -m scripts.evaluate_history --source-database runs/studio.sqlite3 --database runs/history-evaluation.sqlite3 --output runs/history-evaluation.json
python -m scripts.verify_history_migration --source-database runs/studio.sqlite3 --database runs/history-migration.sqlite3 --output runs/history-migration.json
```

命令只读源数据库，需要新的评测数据库。编辑耗时评测默认选择历史内容最多的文档，也可以通过 `--session-id` 指定会话。迁移检查逐项比较原有表、当前画布内容及每个文档的全部历史快照；升级后还可以使用 `--compare-database` 与升级前的备份比较，不修改被比较的数据库。

逐条历史版本的后端检查为 97/97 项通过，新增混合存储保护检查单独运行通过；浏览器检查为 45/45 项通过。真实模型图片修正为 11/11 项通过：整条擦除多余眼睛用时 11.72 秒，局部擦除穿过面部的线段用时 7.67 秒，两轮实际调用橡皮擦，工具错误为零，撤销和重做恢复了对应图形。记录为 `runs/visual-edit-history-rows-20261020.json`。这些结果验证编辑行为，整幅作品质量另行评测。

加入执行次数摘要与按输入范围说明完成条件后，真实子 Agent 生命周期检查为 18/18 项通过，用时 29.16 秒，记录为 `runs/lifecycle-activity-verified.json`。摘要从真实人物任务日志读取 80 次路径重塑记录，最近各对象的修改次数均与原始调用一致，整个执行摘要为 1,921 字符，记录为 `runs/execution-memory-replay.json`。计数在会话压缩与图形变化后保留，不作为操作数量上限。

## 对象检索与上下文概览

对象概览只携带最近或已选中的少量元数据，全部当前对象可通过 `inspect_scene` 按编号、名称和区域分页检索。分页中的编号及续页参数保持结构化，长路径点另行按需取回；每页携带实际画布或私有草稿的内容标识，修改后继续使用旧分页会收到明确错误。

在同一份实际 1,076 个对象的画布上，仅对象概览的 JSON 从 21,837 字符减少为 4,448 字符。两个实际子 Agent 草稿的对应概览分别从 19,728 减少为 4,080、从 20,499 减少为 4,281 字符。比较使用同一批原生对象，记录为 `runs/scene-overview-comparison-20261020.json`。这些是概览大小，未计入图片、近期工具消息或全部模型上下文，也不能据此推断整幅绘图的时间。

后端检查为 103/103 项通过。真实 Ark 模型两次分页与子 Agent 编辑评测均为 12/12 项通过，分别用时 49.51 秒和 59.57 秒，工具错误均为零。子 Agent 读取四页同名笔画，定位首屏之外的唯一红色笔画，保留原编号完成移动，由主 Agent 提交，其他 20 个对象与撤销重做均通过检查。记录为 `runs/scene-paging-model-20261020.json`、`runs/scene-paging-model-final-20261020.json`。该评测验证检索和编辑行为，完整绘画质量仍需另行验收。

同期完整人物试验只发送“画一幅蒙娜丽莎素描图”，使用已保存的实际参考图。试验在逐条历史与对象分页更新前启动，运行 2,489.02 秒后因画面退化被主动停止：已提交画布有 1,076 个对象，两个修正子 Agent 的草稿分别出现重复脸部轮廓和交叉手指曲线，未达到画面要求。记录为 `runs/portrait-trace-plan-20261020.json`，原生画布、PNG、WebM 和子 Agent 私有草稿均保留。任务状态为 `cancelled`，浏览器脚本按未完成处理并返回失败；它没有通过完整人物质量或总耗时验收。该试验复用了已保存参考，不能作为免费公共搜索成功率。

采用逐条历史与对象分页后的完整人物试验实际完成，任务耗时 323.78 秒，生成 1,323 个原生对象，并保存 PNG、WebM 与 Excalidraw 文件，记录为 `runs/portrait-scene-pages-20261020.json`。独立上下文的实际模型评测仅通过 1/4 项：主体与交叠姿态可以辨认，眼睛与瞳孔不完整、手指线条关系不清、主体与背景缺少线条主次。记录为 `runs/portrait-scene-pages-review-20261020.json`。执行完成与画面质量分别记录；该作品没有通过完整人物质量验收，也未验证免费公共搜索。后端同时记录了一次 WebSocket 保活异常，网络稳定性仍需修复。

## 提交前候选图审阅

主 Agent 在提交子 Agent 结果前接收候选 PNG；候选由当前共享画布与未提交动作共同生成。五项自动检查覆盖预览不改变文档和历史、保留后续手工编辑、未经查看与过期候选不可提交、后续动作冲突在提交前发现、其他候选与局部查看、部分提交后恢复、空白候选以及任务归属。使用真实 Chromium 导出器逐像素比较候选 PNG 与实际提交结果，验证同一原生对象的手绘外观一致。

实际 Ark 模型的分页与子 Agent 编辑检查为 14/14 项通过，工具错误为零，用时 54.06 秒；记录为 `runs/result-preview-model-20261020.json`。主 Agent 的模型上下文中包含提交前的 `result_observation_ids`，对应图片在提交前标记为已经发送，之后才执行 `apply_agent_result`。该检查证明图片实际进入上下文，不能单独证明复杂绘画质量。

对应版本的完整后端检查为 108/108 项通过，浏览器检查为 45/45 项通过；记录为 `runs/tests-result-review-full-20261020.stdout.log` 和 `runs/browser-result-review-20261020.stdout.log`。浏览器覆盖连续编辑、撤销重做、导出、设置、滚动、任务重命名与删除、任务窗口隔离。

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

## 工具压缩与长期会话记忆

记忆服务使用确定性摘要、SQLite 搜索和结构化工作说明，不额外请求模型。真实检查在 80 轮绘图后找回早期对象“纪念点”，修改颜色与位置，保存工作记忆，重新打开生产应用后继续移动同一对象。

| 检查 | 结果 |
| --- | --- |
| 早期指令与原对象检索、当前几何取回 | 通过，实际调用 `search_memory` 与 `recall_memory` |
| 第一轮修改并保存工作记忆 | 37.84 秒 |
| 重新打开应用后的继续修改 | 35.97 秒 |
| 记忆持久化、原编号与名称及尺寸保护 | 通过 |
| 其他 79 个对象、工具错误与图片反馈 | 通过 |
| 完整评测 | 12/12 项通过 |

记录为 `runs/memory-real-verified.json` 和同名 SQLite 数据库，两轮工具错误为零。另一次完整评测同样为 12/12 项通过，其中出现一次实际工具错误，已完整进入模型后续上下文，记录为 `runs/memory-real-1.json`。用时包含实际模型检索、读取、修改、保存工作记忆和图片检查，不能作为本地移动命令的执行时间。

另取实际绘图调用中的一份 `inspect_scene` 返回：原始输出 233,138 字符，模型摘要为 4,177 字符，减少约 98.2%。通过二十页读取后与完整原文完全一致。记录为 `runs/memory-compression-audit.json`。该测量针对同一份工具输出，不能据此推算整份上下文或绘图总耗时的变化。

加入记忆服务后的真实子 Agent 生命周期检查为 18/18 项通过，用时 36.97 秒，记录为 `runs/agent-lifecycle-memory-real.json`。内容包括并发请求、关闭恢复、排队输入、原对象编号、提交幂等、私有图片及擦除已分配参考线后继续绘图。

同一代码的浏览器完整检查为 45/45 项通过。真实图片修正检查为 11/11 项通过：整条擦除多余眼睛用时 15.50 秒，局部擦除穿过面部的线段用时 12.00 秒，两轮均实际调用 `erase_strokes`，其他图形与撤销重做检查通过，工具错误为零。记录为 `runs/visual-edit-memory-verified.json`。

```bash
python -m scripts.evaluate_memory --database runs/memory-evaluation.sqlite3 --output runs/memory-evaluation.json
python -m scripts.evaluate_agent_lifecycle --database runs/agent-lifecycle.sqlite3 --output runs/agent-lifecycle.json
```

API 5 的人物质量验证仍未通过。一次生成的图片评价指出颈部与肩部连接、重复轮廓、手部结构和背景交叠仍有问题，对应 `runs/portrait-vision-final-visual-review.json`。接入记忆工具后的独立浏览器验证只发送“画一幅蒙娜丽莎素描图”，手部子 Agent 反复修改同一组轮廓，在 742.46 秒时主动停止并保留未完成作品，未记录为成功。实际作品、录像和工具过程分别为 `runs/portrait-memory-final.png`、`runs/portrait-memory-final.webm` 与同名 SQLite 数据库。复杂人物的画面质量和生成延迟尚未达到验收目标，不能从记忆检查通过推断该目标已经完成。

## 数据与结果解释

所有模型评测都需要实际可用的模型连接；自动化检查使用实际 SQLite 与原生对象，不预设模型返回。作品、录像和完整网页日志可能包含用户内容，默认不加入 Git。命令中的历史数据库及任务编号用于复查本机已有记录，新环境应运行生成命令建立自己的记录。

工具执行成功、对象结构完整、延迟和画面质量分别评价。目前可复现检查覆盖执行流程和指定样本，稳定的低延迟及高质量成图仍需要更多实际样本验证。

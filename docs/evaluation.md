# 真实操作评测

## 子 Agent 草稿纠错闭环（2026-10-20）

新增按输入隔离的草稿复查、重复读取触发、对象与区域反馈、读取及纠错检查点，以及未完成交回主 Agent 的结构化结果。完整后端检查为 188 项通过，记录为 `runs/tests-draft-loop-full-20261020-final.stdout.log`；最终相关记忆与草稿检查为 21 项通过。实际错误原文和已准备动作使用 SQLite 保存，未读错误完整进入后续上下文。

同一份真实停滞人物草稿在独立数据库中继续执行，复查识别了手指环线及腕袖连接问题，子 Agent 删除相关线条并继续重画。一次聚焦反馈检查从恢复到首批修改用时 115.53 秒。该数字只衡量失败草稿的下一批修改，尚未完成整个人物输入，不能视为整幅生成成功或提速比例。记录为 `runs/draft-loop-focused-repair-20261020/report.json`，后续完整纠错保留在 `runs/draft-loop-complete-repair-20261020/`。

同轮浏览器检查定位并修复了远程对象与选择尚未同步时产生的空选择回写。修复后，连续增量、缺失与乱序恢复、重连、双窗口导出、324 个对象下的真实拖动、冲突处理及响应丢失重试通过。记录为 `runs/browser-draft-loop-fixed-20261020.stdout.log`，手势用时 41 毫秒、36 个笔画过程帧、6 次整图复制；这些是交互检查，不能代表模型绘图耗时。

`runs/scene-paging-draft-loop-20261020.json` 的真实模型任务完成了精确移动及对象保护，用时 184.66 秒，原评测有两项检查未通过。日志显示阶段审图的格式错误未进入主 Agent 上下文；该路径随后加入与当前图片匹配的完整错误反馈。分页评测分别检查阶段反馈是否送达、完成是否具有有效图片检查，以及存在成功阶段检查时是否复用，不要求固定的模型请求次数。原失败报告继续保留。

评测日期：2026 年 10 月 19 日至 20 日。环境为 Windows、Python 3.13、Node.js 24、Chrome，实际模型为 Ark Agent Plan `ark-code-latest`。服务、SQLite 和浏览器均在本机运行。当前 API 7 包含实际画布图片检查、橡皮擦、候选参考图预览、区域轮廓与固定计划分页，以及手动变更合并与幂等保存。完整工具过程及作品保存在本机 Git 忽略的 `runs/`；下文人物与多题材生成表保留 API 4 样本的版本背景，画面验收单独记录。

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

`evaluate_quality` 使用任务结束后的实际共享原生文档导出 PNG，候选图片不参与最终作品导出。模型看图的证据单独核对共享画布范围、当前内容标识和实际模型上下文中的图片编号。报告保存实现文件和题目数据的 SHA-256，分别记录候选图数量与最终共享画布的看图状态。

## 完成时独立图片检查

主 Agent 已查看图片仍不足以证明成图合格。一次单次总体评价把有缺陷的实际人物作品判为可完成，未作为最终检查方案；后续逐项评价还出现模型返回无效 JSON，运行时未记录通过。相应实验保留在 `runs/completion-review-model-20261020.json` 与 `runs/completion-review-criteria-model-20261020.json`。原始无效响应现作为有来源和 SHA-256 的解析回归样本保存于 `tests/fixtures/`，不使用伪造模型回复。

当前检查先独立描述实际像素，再逐项判断任务匹配、比例连接、必要细节、线条及构图；失败项目必须给出具体修正建议。实际 Ark 模型的两组完成边界检查均为 9/9 项通过：拒绝之前实际生成的缺陷蒙娜丽莎与捧杯人物，允许原编号红色圆的精确移动，保留另一个对象，不修改原生画布或历史；同一画面正负检查结果均可复用，并且任务之间隔离。记录为 `runs/completion-review-focused-model-20261020.json` 和 `runs/completion-review-portrait-model-20261020.json`。

该版本后端检查为 122/122 项通过，前端同步函数检查为 5/5 项通过，六个完整浏览器检查脚本通过，包括动画中途取消后的保存画面恢复。记录为 `runs/tests-quality-completion-final-20261020.stdout.log` 与 `runs/browser-quality-completion-full-20261020.stdout.log`。

上述边界检查在实际主模型收到图片后，显式调用运行时完成工具，验证拒绝与通过流程；它们不是重新生成完整作品的成功率。缺陷蒙娜丽莎的独立检查耗时 28.60 秒，缺陷捧杯人物为 33.25 秒；对应精确移动的检查为 12.66 秒与 14.39 秒，未改变画面的后续检查未重复发起请求。新增模型检查有实际耗时，完整任务延迟仍需继续优化。

完整实际 Agent 流程另通过 14/14 项分页定位与原对象修改检查：主 Agent 创建子 Agent，子 Agent 读取四页定位目标，主 Agent 查看候选图后提交，独立检查允许完成，其他对象与撤销重做保持一致。用时 68.64 秒，工具错误为零；记录为 `runs/quality-reviewed-scene-paging-20261020.json`。这证明完成检查接入了实际 Agent 流程，不能推出整幅人物、山水或其他题材已经通过。

```bash
python -m scripts.evaluate_completion_review --rejected-artwork PATH.excalidraw --drawing-request "该作品原本应完成的绘图要求" --database runs/completion-review.sqlite3 --output runs/completion-review.json
```

自然交付流程以模型回复中没有工具调用为候选信号。当前真实模型边界检查为 10/10 项通过：拒绝实际缺陷人物图、把完整评审交给下一次模型请求、允许正确局部移动、保持其他对象与历史、复用当前评审并隔离任务。没有说明文字的绘图候选通过图片检查后使用评审摘要。记录为 `runs/natural-completion-boundary-r2-20261020.json`，该检查显式调用实际完成入口，完整成图质量单独验收。

评审格式错误保持未通过状态，完整响应进入后续上下文。边界检查按相同流程发送实际反馈，再请求更正报告，原始格式错误保存在结果的 `schema_errors` 和 SQLite 中；评分提示与五个视觉维度保持固定。独立评审请求不包含绘图工具历史，格式恢复只附加该评审自己的错误。

同版本的完整分页编辑流程为 17/17 项通过，用时 139.44 秒：子 Agent 通过自然回复交付局部候选，主 Agent 预览和提交，当前整图通过独立评审后完成，其他笔画与撤销重做保留。记录为 `runs/natural-completion-scene-paging-20261020.json`。后端检查为 226 项通过；两轮实际说明请求也完成，画布保持不变，记录为 `runs/natural-completion-reply-20261020.json`。这些检查验证自然交付与编辑行为，整幅作品的质量和延迟继续按题目评测。

五类题材仍分别验收。没有独立检查的旧版本山水测试完成于 486.77 秒，但桥岸连接和线条质量仍不满意；同版本捧杯人物在最后保存活动为 3,162.60 秒时仍有头颈比例及握杯关系缺陷，评测进程退出后通过项目的中断恢复流程核实为 `cancelled`。现场及中断过程记录为 `runs/drawing-quality-preview-20261020/portrait-with-prop-cancelled.json`、原生文件和 PNG。它不是完成延迟或成功作品。其余题材的新版实际绘图继续单独评测，不以编辑功能检查代替视觉验收。

新的 `evaluate_quality` 运行会将实际使用的实现文件复制到输出目录的 `implementation/`，与 SHA-256 一起保存。重新检查已有任务时保留原绘图实现、原要求与题目标识，当前评测实现及题目标识另外记录；缺少旧版本标识时保留未知状态，不把当前代码当成旧任务的绘图版本。

## 主 Agent 与精细绘图执行的边界

持续猫图试验暴露出主 Agent 仍可绕过子 Agent 进行全部手工绘图：一次运行中已观察到 150 次主模型响应、47 次对象查询、37 次图片查看及 21 次手工画笔调用，尚未完成，也未派发子 Agent。该记录属于运行中的失败诊断，不是完成耗时或成功率。日志位于 `runs/drawing-quality-reviewed-20261020.sqlite3`。

主 Agent 的精细画笔和曲线重塑现交给子 Agent，模型工具定义及运行时共同检查；简单几何、语义编辑与确定性描线计划保留。几何压缩与修订历史的自动检查使用真实私有草稿、原生准备动作及 SQLite 记录，随后通过主执行器提交，不伪造模型回复。对应完整后端检查为 128 项通过，真实模型分页与原对象编辑为 14/14 项通过，用时 67.77 秒；其中一次工具错误已进入后续模型上下文并恢复，不能写作零错误。记录为 `runs/tests-main-child-roles-full-20261020.stdout.log` 和 `runs/main-role-scene-paging-20261020.json`。新版整幅猫图已实际派发两个绘图子 Agent，完整画质与总耗时仍单独观察。

`quality_evaluation.js` 通过隔离的生产 HTTP 服务执行题材评测，在输出目录写入 `server.json`，包含本次测试服务地址和独立数据库路径。需要停止某个无效修补案例时，可对该测试地址使用正常的 `/api/studio/tasks/{id}/cancel`，保存取消状态和当前作品后继续后续案例。它要求全新数据库，避免打开现有用户库。实现归档覆盖全部 `studio/`、`server/` Python 源码、渲染器、前端依赖锁定文件以及评测和录制脚本，包含实际角色工具定义。

`mona-lisa-sketch` 使用原样的单句“画一幅蒙娜丽莎素描图”，不附加预先指定来源或几何。该案例通过实际图片检查构图、面部、双手、衣着和远景，不以对象名称或笔画数量代替这些可见特征。它与原有五类题材共享同一个生产运行时；`DRAW_AGENT_QUALITY_CASES=mona-lisa-sketch` 可单独录制和评测。

该流程同时打开真实编辑器，为每题单独保存 `.webm`、`.browser.png` 和 `.recording.json`，并保存 Python 导出的最终原生文档及 PNG。录制信息包含任务、画布、开始时间、实际收到的增量与画笔过程事件数和浏览器错误。快速指令可能在页面打开前已经完成，此时事件数为零，不能把静止录像当作逐步绘图证明。取消或失败的任务保留其实际状态。`DRAW_AGENT_QUALITY_CASES` 可指定逗号分隔的案例编号，`DRAW_AGENT_QUALITY_DATASET` 可指定自定义数据集，`DRAW_AGENT_EVAL_DIR` 可指定独立输出目录。录制错误单独保存在 `recording-errors.json` 并使检查失败。

## 连续作画事件与断线恢复

API 7 的当前检查为 166 项后端测试、11 项前端同步与编辑状态测试、7 组浏览器检查全部通过。真实鼠标拖动的保存请求被暂缓时，通过生产 API 执行实际绘图指令，验证本地移动在 Agent 更新后仍显示、移动与改色自动合并、同位置冲突保留草稿及两种明确处理选择。确认删除仍能删除存在本地冲突的当前会话。另一次网络故障注入让后端真正提交操作后故意丢弃响应，重试时对象、文档版本和撤销历史不重复变化。

密集画布使用 324 个原生对象，收到 36 个笔画过程帧和 5 个画布增量，完整显示文档只复制 6 次，最终导出的原生内容与数据库一致。该数字衡量整图复制次数，不代表整幅作品或模型调用的耗时变化。最终日志为 `runs/browser-manual-race-final-20261020.stdout.log`；浏览器设置组没有进行真实模型连接测试。该轮交互检查使用实际原生对象及确定性绘图指令，不预设模型响应。另一个用时 36 毫秒的实际鼠标手势在远端更新后立即进行，经过程序更新保护阶段仍能正确保存；画布切换会清理旧阶段的回调。

原用户数据库只以只读方式建立备份，升级检查在独立副本中进行。6 个会话的文档、全部原有表、历史内容和顺序均保持一致，SQLite 与外键检查通过，记录为 `runs/manual-sync-upgrade-20261020/preservation.json`。尚未保存的浏览器本地草稿当前不支持跨刷新离线恢复。

蒙娜丽莎最小提示词的本轮质量诊断最终主动取消：共享画布保留 629 个对象，录像记录 629 个增量与 2,672 个笔画过程事件，无浏览器错误，但私有修补多次增加错位和重叠轮廓，画质未通过。取消时记录 1,975.65 秒，这是未完成运行的已用时间，不是成功成图延迟。该次实际服务使用之前归档的代码快照，最终报告没有有效独立审图结论；不能用本次交互检查推断这幅人物已经合格。文件为 `runs/quality-mona-polygon-policies-20261020/mona-lisa-sketch.json`、`.webm` 和 `last-private-draft.excalidraw`，停止原因另存 `diagnostic-stop.json`。

画布、撤销历史、提交进度和增量事件使用同一事务。新增检查覆盖事件插入失败后的完整回滚、两个 SQLite 写入连接的版本顺序、会话文档与游标的一致读快照、503 条事件跨页回放、旧客户端快照兼容、删除通知，以及客户端文字消息与画布通知同时到达时的事件交付。

该版本后端检查为 117/117 项通过，前端同步函数检查为 5/5 项通过；完整浏览器检查继续通过，并新增连续更新、双窗口原生导出一致、丢消息与乱序后的恢复、断线回放和画布切换检查。记录为 `runs/tests-scene-events-full-20261020.stdout.log` 和 `runs/browser-scene-events-full-20261020.stdout.log`。测试发现并修复 Excalidraw 自动规范化元数据被当成手工绘图保存的问题；规范化元数据不再使旧几何回写。

读取已经完成的 1,323 对象人物作品归档，1,324 个完整画布事件的 JSON 负载合计为 764,807,027 字节；相同版本的增量负载为 2,529,465 字节，减少 99.67%。前端实际使用的处理函数逐版本回放后，最终对象内容、图层顺序和选择状态一致。记录为 `runs/portrait-scene-event-replay-20261020.json` 及其 `.replay.json`。这是已保存实际数据的回放，没有重新生成绘画或发送模型请求，也没有删除原归档。

同一幅人物作品还通过真实浏览器与生产 HTTP/WebSocket 服务进行了连续移动、丢消息、乱序、断线和画布切换检查。38 个收到的增量消息合计 76,582 字节，对应完整文档为 42,548,438 字节；两个窗口的原生导出均与当前服务端几何一致，浏览器错误为空。记录为 `runs/browser-scene-events-portrait-20261020.stdout.log`。这些字节数为 JSON 负载，不能直接当作模型耗时或压缩后的网络流量。长时间连接的保活稳定性仍需持续观察。

补充真实画笔动画的中途取消检查：同一版本的恢复快照仍需应用，以移除尚未提交的临时笔画。取消后浏览器原生导出与服务端保存内容一致；记录为 `runs/browser-stroke-cancel-fixed-20261020.stdout.log`。

```bash
python -m scripts.evaluate_scene_events --source-database ARCHIVE --output runs/scene-events.json
python -m scripts.verify_browser --database runs/scene-sync-browser.sqlite3 --scripts scene_sync_smoke.js
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

新增 Commons 目录后，真实检查搜索了人物版画、山地绘画和坐姿猫三类资料，并用实际 Ark 图片请求判断每张参考的可用性。三题均找到至少一个可用来源，九张候选中七张通过图像检查，另外两张保留为不合适的候选；实际像素解码、独立存储、缓存与画布隔离检查通过。记录为 `runs/commons-reference-quality-v2-20261020.json`。这是资料检索与参考适用性检查，不是整幅作品成功率。原始图片和完整筛选日志保存在同名 SQLite，公共服务的后续可用性仍取决于实际网络返回。

```bash
python -m scripts.evaluate_reference_images --source commons --database runs/commons-reference-check.sqlite3 --output runs/commons-reference-check.json
```

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

`reference-layout` 验证主 Agent 定义参考布局、子 Agent 直接在局部来源 PNG 上选择归一化关键点、主 Agent 审阅并提交的完整流程。生成的参考图包含三角轮廓及应排除的独立短线；目标使用负画布坐标、非等比例目标画幅和居中留白。原有蓝圆必须保持，最终内容为可编辑原生路径，撤销重做恢复相同对象。它与已有 `reference-polygon` 共用驱动，通过不同方法分别验证手工源图落笔和自动轮廓提取，原有默认行为保留。

```powershell
$env:DRAW_AGENT_EVALUATOR='reference-layout'
$env:DRAW_AGENT_EVAL_DIR='runs/reference-layout-check'
python -m scripts.verify_browser --database runs/reference-layout-check.sqlite3 --scripts quality_evaluation.js
Remove-Item Env:DRAW_AGENT_EVALUATOR
```

第一次真实运行因引用汇总读取布局元数据中不存在的 `title` 退出，失败记录为 `runs/reference-layout-model-20261020/report.json`。修复后两次真实运行均为 13/13，用时 102.77 秒和 60.19 秒，分别保存在 `runs/reference-layout-citations-20261020/` 和 `runs/reference-layout-final-20261020/`。后一次包含 10 次实际模型上下文、4 次画布增量、20 次笔画过程事件，工具和浏览器错误均为零；原生折线与理想投影的最大几何距离约 7.00 个画布单位，即 1.75 个源像素，在预先设定的 5 源像素范围内。该指标包含模型选择关键点的误差；数学映射、完整图与局部图一致性、隔离、重新打开和历史由原生几何回归检查覆盖。本轮完整后端检查为 176 项通过。

这两次耗时不是受控的提速对照，局部接口检查也不能代替人物、山水等整幅作品验收。归一化接口版本的蒙娜丽莎测试仍失败：身体子 Agent 在最后一次修改后又执行了至少 100 次只读工具调用，已准备动作保持为 140 条，手部及比例缺陷没有解决。本次独立评测通过正常取消接口停止，保留 `runs/quality-mona-normalized-20261020/diagnostic-stop.json`、只读循环记录、共享画布、两份私有原生草稿与对应 PNG、录像。它不作为完成延迟或成功作品；停止该评测没有为产品增加模型调用数或执行时间限制。

`observed-coordinates` 使用负画布坐标的原生简笔人脸与远处蓝圆，要求真实主 Agent 派发子 Agent，通过图片坐标擦除指定红线、改嘴并补鼻子，再审阅和提交。它保存实际编辑器录像、原生文件、全图和局部图，并检查三个工具、对象保护、几何位置、撤销重做和错误回传。原生初始画布是显式测试夹具，不预设或替换任何模型响应；该检查验证局部看图修正，不作为完整人物作品验收。

当前默认要求三个工具使用 `coordinate_space=image_normalized`；原有模式可用 `evaluate_observed_coordinates --coordinate-space image_pixels` 复查，几何验收范围不随模式改变。下面列出的三个早期结果均为原始像素模式，保留其当时源码、请求和失败记录。

```powershell
$env:DRAW_AGENT_EVALUATOR='observed-coordinates'
$env:DRAW_AGENT_EVAL_DIR='runs/observed-coordinate-check'
python -m scripts.verify_browser --database runs/observed-coordinate-check.sqlite3 --scripts quality_evaluation.js
Remove-Item Env:DRAW_AGENT_EVALUATOR
```

运行需要真实的图片和工具模型配置，以及尚不存在的独立测试数据库。两次早期实测均完成工具和提交流程，但微笑宽度与鼻子位置未通过预设几何检查，分别用时 95.52 秒和 98.53 秒，记录保留在 `runs/observed-coordinate-repair-20261020/report.json` 与 `runs/observed-coordinate-focused-20261020/report.json`。第二次调整了任务中关于五官比例的说明，因此两次结果不构成受控延迟对照；它们不能记为全部通过或整幅作画成功。

增加局部像素刻度后的同一流程通过 11/12 项，用时 96.85 秒，仍因鼻子位置未满足预设几何范围而失败。模型实际使用三个图片坐标工具，主 Agent 审阅并提交，保护对象和撤销重做正确；模型上下文共 12 次，录像记录 6 次画布增量、2 次笔画过程事件及零浏览器错误。记录为 `runs/observed-coordinate-guided-20261020/report.json`，包含原生文件、全图和 `observed-coordinates.face.png`。这说明精确坐标换算与模型选择正确落点是不同问题，不能将运行完成或无工具报错作为质量通过。该轮后端 171 项、前端状态检查 11 项通过。

只读定位对照 `evaluate_visual_grounding` 使用实际 Excalidraw 渲染的相同人脸，要求真实模型报告红线、双眼和嘴的可见中心。16 次请求对照图片尺寸、网格、全图与局部同时输入，以及像素/归一化输出；画布和历史保持不变。`runs/visual-grounding-20261020/report.json` 与 SQLite 保存原始图片、实际响应和逐点误差。1280 长边单图的像素输入在 8 个定位结果上平均误差为 32.66 个画布单位；归一化输入的同组平均误差为 0.65，最大 1.14。全图与局部同时输入时，分别为 32.09 和 1.19。它支持当前 Ark 配置改用明确的归一化接口，不证明其他模型、其他题材或完整作画均达到相同精度；两次重复不足以估计普遍成功率。

归一化接口后的完整修正复测仍为 11/12 项，用时 94.74 秒：鼻子位置通过，嘴巴宽度仍未满足原有几何条件。结果保留为失败，几何判定范围没有放宽。一次 `wait_agent.after` 类型错误完整进入后续上下文，其他对象、原嘴巴编号和撤销重做保持正确，录像记录零浏览器错误。记录为 `runs/observed-coordinate-normalized-20261020/report.json`。本轮完整后端检查为 172 项通过。

```powershell
python -m scripts.evaluate_visual_grounding --database runs/visual-grounding-check.sqlite3 --output-dir runs/visual-grounding-check --repeats 2
```

多边形选区、原生笔画采样和提示词压缩后的完整后端检查为 157/157。新增的 Chromium 检查先用稀疏闭合点列复现实际导出缺线，再检查补充采样后的同一中心线、边缘像素、SQLite 重新打开和撤销重做。参考提取检查还覆盖旧计划继续使用旧点列、选区不改变共同映射，以及不同选区在压缩后仍保持独立进度。

真实 Ark 只读请求对照使用同一组当前工具约束，对比旧角色说明及带展示标题的 schema 与压缩版本。主 Agent 请求输入从 12,237 token 降至 9,617，子 Agent 从 10,529 降至 8,778；四次请求均实际调用对象查询，原生画布和历史未变。记录为 `runs/prompt-context-final-20261020.json`。这只是请求前缀测量：该次主 Agent 请求用时 5.25/2.57 秒，子 Agent 2.68/4.61 秒，缓存命中也不同，不能据此宣称整幅作画更快。

```powershell
python -m scripts.evaluate_prompt_context --baseline-revision b309052 --database runs/prompt-context-check.sqlite3 --output runs/prompt-context-check.json
$env:DRAW_AGENT_EVALUATOR='reference-polygon'
$env:DRAW_AGENT_EVAL_DIR='runs/reference-polygon-check'
python -m scripts.verify_browser --database runs/reference-polygon-check.sqlite3 --scripts quality_evaluation.js
Remove-Item Env:DRAW_AGENT_EVALUATOR
```

多边形真实模型检查使用独立数据库和生成的三角轮廓参考图，检查子 Agent 分页提取、排除边角背景、主 Agent 审阅并提交、共同映射、其他对象保护和撤销重做，同时录制实际编辑器。它验证选区及执行能力，不作为人物或山水的整幅作品验收。首次运行发现原生画笔末段缩短后被取消，失败记录仍保留在 `runs/reference-polygon-model-20261020/report.json`；修复后的结果单独记录，不覆盖失败样本。

原生采样修复后的首次真实执行检查为 11/11，通过并保留 4 次画布增量、20 次笔画过程事件以及无浏览器错误的录像。记录为 `runs/reference-polygon-native-samples-20261020/report.json`。但其独立审图报告连续加入未定义字段，6 次错误全部进入主 Agent 上下文，总耗时 225.21 秒，不能视为低延迟达标。该次原始错误已保存为 parser 回归样本；新增恢复逻辑将错误反馈给审图模型，严格校验条件保持不变，复测使用新的独立数据库。

加入格式恢复后的真实执行复测同样为 11/11，记录为 `runs/reference-polygon-reviewer-recovery-20261020/report.json`，录像包含 6 次画布增量和 60 次笔画过程事件，无浏览器错误，用时 176.35 秒。该次发生了一次把 Agent ID 放进对象记忆列表的工具错误，已完整返回后续上下文；审图格式错误没有再次出现，因此该样本不证明格式恢复曾被真实触发。格式恢复的校验、完整错误传递及任务/画面/配置隔离由实际历史响应的回归检查覆盖。评测期间本机出现内存压力，不能由两次耗时推算优化比例。后续增加了对象编号字段说明和包含具体错误编号的修正指引。

相同版本的六组浏览器检查全部通过，覆盖删除与恢复、手工画图和历史、七种导出组合、设置与任务隔离、重命名及滚动布局、丢失/乱序事件恢复和取消笔画。日志为 `runs/browser-polygon-native-20261020.stdout.log`；设置组未进行真实模型连接测试，真实 Ark 请求由前述独立模型检查覆盖。

密集曲线读取加入了可选的几何误差参数。真实 Ark 检查为 17/17 项通过：子 Agent 使用 `point_tolerance=1` 将一条 601 采样点的曲线作为 4 点序列读取，原对象精确移动、其他对象保护、阶段审图和撤销重做均通过，用时 83.52 秒，工具错误为零。记录为 `runs/compact-curve-scene-paging-20261020.json`。对两份实际暂停草稿的只读测量中，84 条路径的读取采样点从 2,854 降至 550，最大偏差为 0.997 个画布单位以内；记录为 `runs/compact-path-real-drafts-20261020.json`。这些是几何读取与编辑检查，不能据此推算完整绘画提速比例。本地完整后端检查为 145/145。

Commons 原图恢复的网络检查故意使用不存在的公开缩略图地址，实际取得 HTTP 404 后，通过同条真实目录记录的原图地址取得并解码了 1,455 × 2,048 的来源。记录为 `runs/commons-original-fallback-network-20261020.json`，明确标记为网络故障注入，不伪装成自然发生的故障或完整作画成功。

阶段审图接入后，一次真实 Ark 分页定位与精确移动检查为 16/16 项通过，用时 67.80 秒，工具错误为零。日志确认主 Agent 提交子 Agent 结果后，独立评语实际进入下一轮主 Agent 上下文，完成时复用同一评语，没有重复两次图片检查请求。原对象编号、其他 20 个对象和撤销重做保持正确。记录为 `runs/stage-review-scene-paging-20261020.json`，这是编辑执行检查，不是整幅作品验收；本地完整后端检查为 137/137。

原生填色与层级检查使用实际 Chromium/Excalidraw 渲染和 SQLite 提交。白色闭合画笔位于蓝线前方时交叠像素为白色，移到后方时为蓝色；撤销恢复原顺序及白色遮挡。可见区域擦除的准备、重放、提交、撤销和重做也经过检查，避免提交时误擦被遮住的背景。新增检查位于 `tests/test_layers_and_fill.py`；本地完整后端检查为 135/135，通过不代表完整人物或山水作品已达到验收质量。

所有模型评测都需要实际可用的模型连接；自动化检查使用实际 SQLite 与原生对象，不预设模型返回。作品、录像和完整网页日志可能包含用户内容，默认不加入 Git。命令中的历史数据库及任务编号用于复查本机已有记录，新环境应运行生成命令建立自己的记录。

工具执行成功、对象结构完整、延迟和画面质量分别评价。目前可复现检查覆盖执行流程和指定样本，稳定的低延迟及高质量成图仍需要更多实际样本验证。

## 子 Agent 图片前缀与查询延迟（2026-10-21）

当前参考图和画布图片安排在子 Agent 的角色、任务之后，工具历史及记忆之前。每轮仍提供相同的图片、目标、工具记录和完整错误；读取期间图片保持不变时，模型服务可以复用更多前缀缓存。主 Agent、工具、模型配置、绘图执行和评分规则保持原样。

真实 Ark 对照使用两份实际原生草稿，每份进行四组配对请求，分别按 ABBA 和 BAAB 顺序交错发送。每对请求只调整消息顺序，逐条消息的内容和数量相同，实际查询参数及画布、历史保持经过检查。首组用于暖缓存，表格为其余三组的请求中位数：

| 原生草稿 | 原顺序 | 图片提前 | 暖缓存增加 |
| --- | --- | --- | --- |
| 山水草稿 | 3.96 秒 | 3.09 秒 | 每次 3,072–4,096 token |
| 人物草稿 | 4.17 秒 | 3.70 秒 | 每次 3,072–4,096 token |

16 次真实请求均执行了指定的只读查询，每对请求的 prompt token 总量相同。人物组有一次调整后的请求更慢，不能保证每次请求提速。记录为 `runs/observation-order-cache-20261021/report.json` 与 `runs/observation-order-cache-portrait-20261021/report.json`。这些测量只覆盖已有图片和变化工具历史下的查询，不代表整幅绘画完成时间或画质收益。

同轮完整后端检查为 268 项通过，包含真实截断响应回放、实际工具错误原文、原生 PNG、工具调用返回配对及图片前缀的内容保留。实际模型续作检查保持同一输入，由主 Agent 确认继续，动作从 48 条增加至 54 条，原错误和当前图片送达检查通过，用时 51.58 秒。它与历史续作样本的模型调用选择不同，不构成受控延迟对照。

真实模型的密集曲线分页、读取与移动检查为 18/18：子 Agent 定位后续页中的指定笔画，读取 601 点曲线的简化当前几何，精确移动同一对象，主 Agent 查看提案并提交，其他 20 个对象、图片反馈、阶段评审及撤销重做均通过。记录为 `runs/observation-order-scene-paging-direct-20261021.json`，用时 119.32 秒，工具错误为零。这项检查验证编辑行为，未衡量整幅绘画提速或画质。

诊断命令需要实际模型配置和现有记录的 `run_id`；源数据库只读打开，新数据库及输出目录必须尚不存在。查询词应对应所选草稿的对象名称：

```bash
python -m scripts.evaluate_observation_order --source-database runs/source.sqlite3 --run-id <run_id> --database runs/observation-order.sqlite3 --output-dir runs/observation-order --queries 主松 溪 山 桥
```

可用 `--first candidate` 反向交错首轮顺序。输出保留实际模型返回、token 使用、只读工具返回、消息内容一致性、源文件快照和诊断脚本 SHA256。该脚本独立记录查询性能，不替换冻结的绘图质量评审及晋级条件。

# 真实操作评测

评测日期：2026 年 10 月 19 日。环境：Windows、Python 3.13、Node.js 24、Chrome、Ark Agent Plan `ark-code-latest`。服务和浏览器均在本机运行。下列结果是本次测试记录，样本数量见各项说明。

| 场景 | 结果 | 耗时 |
| --- | --- | --- |
| 同一画布连续创建、移动、选择、撤销、重做、画笔笑脸 | 6/6 通过 | 中位数 15 毫秒；95 百分位 1.76 秒 |
| Agent 根据上一轮指代移动原对象并改变填充 | 3/3 次通过 | 4.26、5.35、6.24 秒；中位数 5.35 秒 |
| 房子、两棵树、太阳与云朵并行绘制 | 4/4 次通过；每次有 3 个子 Agent | 43.21、38.25、39.05、50.38 秒；中位数 41.13 秒 |
| 浏览器手工绘制矩形、撤销、重做、刷新恢复、PNG 导出 | 5/5 项通过 | 浏览器脚本完成一次完整检查 |

连续编辑评测使用明确指令的本地动作路径，画笔笑脸会分帧显示。多轮指代评测使用真实 Ark 模型，并检查对象 ID 保持不变、向右移动 70 像素和绿色填充。并行绘制也使用真实 Ark 模型调用及工具校验，四次分别提交 41、35、34、38 个原生 Excalidraw 对象；除任务状态外，还检查房子、两棵树、太阳、云朵和光芒的位置。主 Agent 只读取每个子 Agent 的 JSON 摘要和动作概览，完整工具响应写入 SQLite。录制脚本在真实浏览器中依次创建、移动与画笔绘制，视频位于本机 `runs/draw-agent-demo.webm`。

复现命令：

```bash
python -m scripts.evaluate_studio
python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000
python -m scripts.evaluate_agent_edit
python -m scripts.evaluate_multiagent
cd frontend-app
node scripts/browser_smoke.js
node scripts/record_demo.js
```

两项模型评测需要运行中的服务和设置页保存的真实模型连接。重复运行评测时可用 `--output` 指定不同的 JSON 文件；`evaluate_multiagent` 用 `--session-id` 可重新检查已有画布。画布和评测数据保存在 Git 忽略的 `runs/`。这些样本只说明指定场景的运行结果，还不能代表其他题材或长期服务的成功率。

## 模型设置与会话隔离检查

同日使用独立 Python 环境和新版模型适配器运行以下检查，模型仍为 Ark Agent Plan `ark-code-latest`：

| 场景 | 结果 | 说明 |
| --- | --- | --- |
| 后端自动化检查 | 20/20 通过 | 包含配置加密、重启读取、配置版本、全局运行限制、任务上下文、会话事件、名称保存与历史隔离 |
| 模型连接验证 | 通过 | 实际调用工具并消费工具结果后返回文本；两次检查分别为 5.62 秒、4.15 秒 |
| Agent 多轮指代编辑 | 1/1 通过 | 保留对象 ID，向右移动 70 像素并改为绿色，3.73 秒 |
| 三个子 Agent 并行绘图 | 1/1 通过 | 41 个对象，房子、两棵树、太阳与云朵位置检查全部通过，41.38 秒 |
| 设置页、画布切换与双窗口检查 | 7/7 通过 | 保存设置、密钥不回传、运行期间阻止保存、消息与图形归属、双窗口隔离；额外通过设置页真实模型连接测试 |
| 长列表、长对话与名称编辑 | 9/9 通过 | 独立滚动、刷新恢复、名称校验、取消、新建命名、移动窗口尺寸、设置页滚动及双窗口名称同步 |

结果文件位于 `runs/settings-agent-edit-evaluation.json` 与 `runs/settings-multiagent-evaluation.json`。设置及窗口检查运行 `node frontend-app/scripts/settings_smoke.js`，覆盖真实会话切换、双窗口独立画布、消息归属、其他画布运行时禁止保存、保存后刷新恢复及密钥不回传。该脚本不需要模型凭据；配置真实模型后设置 `DRAW_AGENT_TEST_MODEL=1` 可额外验证设置页的实际连接测试。

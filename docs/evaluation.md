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

两项模型评测需要运行中的服务和 `ARK_API_KEY`。重复运行评测时可用 `--output` 指定不同的 JSON 文件；`evaluate_multiagent` 用 `--session-id` 可重新检查已有画布。画布和评测数据保存在 Git 忽略的 `runs/`。这些样本只说明指定场景的运行结果，还不能代表其他题材或长期服务的成功率。

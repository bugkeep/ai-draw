# 参与 Draw Agent Studio 开发

欢迎通过 GitHub Issue 报告故障或讨论功能。请写清操作步骤、预期画面、实际画面、浏览器与服务端版本。涉及 Agent 的问题，请附上任务状态、画布对象信息和去除密钥后的日志；不要提交 `.env`、API Key 或用户画布数据。

修改代码时，为当前任务使用一条分支，并在 Pull Request 中关联对应 Issue。提交前运行：

```bash
python -m pip install -r requirements-dev.txt
cd frontend-app
npm ci
npm run build
cd ..
python -m pytest -q
```

涉及编辑器交互的修改，还应启动服务并运行 `node frontend-app/scripts/browser_smoke.js`。涉及 Agent 执行的修改，应运行 `python -m scripts.evaluate_studio`；具备 Ark 凭据时，再运行 `python -m scripts.evaluate_agent_edit` 和 `python -m scripts.evaluate_multiagent`。在 Pull Request 中说明实际执行的检查和结果。

Pull Request 合并后及时删除对应的本地和远端任务分支，保持工作分支数量较少。

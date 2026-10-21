# Contributing

[简体中文](CONTRIBUTING.zh-CN.md) · **English**

Use [Issues](https://github.com/bugkeep/ai-draw/issues) to report bugs, suggest improvements, or share drawings. English and Chinese issue templates are available.

## Report a problem

Include the steps to reproduce, expected behavior, actual behavior, browser, Python version, and Node.js version. For drawing-agent problems, include the task status and relevant logs with credentials removed.

Do not include `.env` files, API keys, private canvas data, or the application database.

## Submit a change

Create a branch for your change and open a pull request that links to the relevant issue. Explain the resulting behavior and the checks you actually ran. Remove completed task branches after the pull request is merged.

From the project directory, install development dependencies and run the standard checks:

```bash
python -m pip install -r requirements-dev.txt
cd frontend-app
npm ci
npm run build
npm run test:sync
cd ..
python -m pytest -q
```

## Check browser changes

Use a dedicated test database. The browser checks create canvases, change drawing content, and may save model settings.

To run the browser verification used by CI:

```bash
python -m scripts.verify_browser --database runs/contribution-browser.sqlite3
```

For individual frontend checks, start a separate test service and point `DRAW_AGENT_URL` at that service. Available checks include:

| Area | Script |
| --- | --- |
| Editor interactions | `frontend-app/scripts/browser_smoke.js` |
| Model settings | `frontend-app/scripts/settings_smoke.js` |
| Canvas names and scrolling | `frontend-app/scripts/sessions_smoke.js` |
| Canvas deletion | `frontend-app/scripts/deletion_smoke.js` |
| Export | `frontend-app/scripts/export_smoke.js` |
| Scene synchronization | `frontend-app/scripts/scene_sync_smoke.js` |
| Manual edits | `frontend-app/scripts/manual_edit_smoke.js` |

## Check drawing-agent changes

With a real model connection configured, run the checks relevant to your change:

```bash
python -m scripts.probe_model
python -m scripts.evaluate_studio
python -m scripts.evaluate_agent_edit
python -m scripts.evaluate_multiagent
```

For complex flowchart or continuous-editing changes, also run `python -m scripts.evaluate_transformer` and record the resulting diagram, model time, and editing behavior.

Model checks perform actual requests and may incur provider charges. Include the commands and observed results in the pull request.

[Development](docs/development.en.md) · [Deployment](docs/deployment.en.md) · [Model setup](docs/model-settings.en.md)

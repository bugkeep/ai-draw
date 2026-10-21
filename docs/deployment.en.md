# Deployment

[简体中文](deployment.md) · **English**

## Run locally

Install **Python 3.11+** and **Node.js 20.19+**. Use a dedicated Python environment. On Windows, install Chrome; on other systems, install Playwright Chromium with the command below.

From the repository directory:

```bash
python -m pip install -r requirements.txt
cd frontend-app
npm ci
npm run build
npx playwright install --with-deps chromium
cd ..
python run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Go to **Settings** (`设置`) and save a model connection with image input and tool calling. See [Model setup](model-settings.en.md).

Node.js, a browser, and the built frontend are required at runtime. On Windows, image rendering uses the installed Chrome by default; other systems use Playwright Chromium. Set `CHROME_PATH` to use another Chromium executable.

## Host the service

The same service handles the frontend, HTTP API, and WebSocket connections. Run one Uvicorn worker:

```bash
python -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8000
```

For access outside the host, put an authenticated HTTPS reverse proxy in front of the service and forward WebSocket connections. Keep Uvicorn bound to the local address. The model connection is shared by the agents using this service.

## Data and backups

The default database is `runs/studio.sqlite3`. Set `DRAW_AGENT_DB` to change its location. It contains canvases, conversations, tasks, drawing history, model settings, reference images, and agent records.

API keys are encrypted using the key file at `model-settings.key` beside the database. Set `DRAW_AGENT_SETTINGS_KEY_FILE` to change that path. A usable backup must include both the database and its encryption key file.

Use SQLite's backup interface while the service is running. After stopping the service, you may copy the database and its `-wal` and `-shm` files if present. Restore a missing encryption key from backup before starting the service.

Image observations, reference images, and complete tool records can increase database size during long drawing sessions. Deleting a canvas also deletes its associated history and image records.

## Verify the installation

`GET /api/studio/health` reports whether a model has been configured. After saving a connection, run:

```bash
python -m scripts.probe_model
```

Additional checks:

```bash
python -m scripts.evaluate_studio
python -m scripts.evaluate_multiagent
```

These checks create test sessions and make actual model requests. Provider charges may apply.

## Update the application

Update the code, install dependencies, and rebuild the frontend. Stop the existing Uvicorn process, start it again, and refresh the browser.

The interface checks the service API revision. If the frontend and service versions do not match, it shows a notice and temporarily disables editing until a matching service reconnects. Existing canvases and history remain in the database.

[Model setup](model-settings.en.md) · [Development](development.en.md) · [Contributing](../CONTRIBUTING.md)

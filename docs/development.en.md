# Development

[简体中文](development.md) · **English**

Draw Agent Studio edits a persistent canvas through conversation. Users can also select objects, draw with the pen, add text, and export editable work.

## Run the project

Follow [Deployment](deployment.en.md) for dependencies and startup, then configure a connection using [Model setup](model-settings.en.md).

For frontend development:

```bash
cd frontend-app
npm ci
npm run dev
```

Vite proxies API and WebSocket requests to the backend. Build the frontend with `npm run build` before using the normal application server.

## Project structure

| Location | Responsibility |
| --- | --- |
| `frontend-app/` | Canvas editor, drawing conversation, settings, and export. |
| `server/` | Application entry point and service configuration. |
| `studio/actions.py` | Drawing-action validation and native Excalidraw objects. |
| `studio/pen.py` and `studio/pen_geometry.py` | Pen paths, curve changes, and coordinate conversion. |
| `studio/eraser.py` | Whole-stroke and partial erasing. |
| `studio/renderer.py` and `studio/vision.py` | Canvas images and drawing observations. |
| `studio/agents.py` and `studio/agent_tools.py` | Model interaction and drawing tools. |
| `studio/agent_manager.py` and `studio/agent_store.py` | Drawing-agent lifecycle, private drafts, and persisted records. |
| `studio/runtime.py` | Drawing tasks, action submission, and cancellation. |
| `studio/manual_edits.py` | Manual edits and conflicts with incoming changes. |
| `studio/settings.py` and `studio/llm.py` | Model connections, connection testing, and encrypted settings. |
| `studio/store.py` | Canvas state, versions, history, and task records. |
| `scripts/` | Browser verification and actual model evaluations. |
| `tests/` | Automated checks. |

## Drawing and editing

The application supports rectangles, ellipses, diamonds, text, lines, arrows, and pen objects. Model-generated work remains editable on the canvas and in Excalidraw exports.

The main agent coordinates composition and accepts drawing-agent results. Drawing agents prepare private drafts. The main agent can inspect draft images before submitting changes to the shared canvas. Image checks compare the current work with the request and can ask the agent to continue editing.

Each canvas has its own conversation, task queue, drawing events, and undo history. Manual changes are saved as object updates. Changes to different objects can be combined; conflicting edits show a choice in the interface.

The application database also stores observations, tool records, drawing references, and agent memory. Use a dedicated database for experiments and browser checks.

## Verification

See [Contributing](../CONTRIBUTING.md) for standard checks and the browser-test list. CI installs Python and frontend dependencies, audits frontend packages, builds the frontend, checks scene synchronization, runs Python tests, and verifies the editor in a browser.

Real-model evaluations require a configured connection and may incur provider charges. Report which checks ran, the model used, and the observed result.

## Detailed design notes

The following internal design notes are currently in Chinese:

- [Application architecture](studio-architecture.md)
- [Drawing tools and reference search](drawing-tools.md)
- [Drawing-agent lifecycle](agent-lifecycle.md)
- [Agent memory](agent-memory.md)
- [Canvas context inspection](context-inspection.md)
- [Drawing specification](drawspec.md)

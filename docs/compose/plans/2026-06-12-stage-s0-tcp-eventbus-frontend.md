# Stage S0: TCP Framework, Event Bus & Frontend Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foundational TCP server framework, Event Bus system, and basic frontend structure for the AI voice drawing tool.

**Architecture:** TCP daemon server accepts connections, dispatches events through EventBus, and returns results. Frontend provides voice input (Web Speech API), canvas (Fabric.js), and TTS feedback via HTTP API.

**Tech Stack:** Python (asyncio, FastAPI), HTML/CSS/JS (Fabric.js, Web Speech API)

---

## File Structure

```
voice-draw/
├── agent/
│   ├── __init__.py
│   ├── daemon.py              # TCP server daemon
│   └── main.py                # Agent entry point
├── events/
│   ├── __init__.py
│   └── base.py                # Event types and EventBus
├── server/
│   ├── __init__.py
│   ├── app.py                 # FastAPI app
│   └── routes.py              # HTTP routes
├── frontend/
│   ├── index.html
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js
│       ├── speech.js
│       └── canvas.js
├── requirements.txt
└── run.py                     # Entry point
```

---

### Task 1: Event Bus Implementation

**Covers:** §2.1 Event Bus, §3.1 Event Bus

**Files:**
- Create: `events/__init__.py`
- Create: `events/base.py`

- [ ] **Step 1: Create events package init**

```python
# events/__init__.py
from .base import EventBus, BaseEvent, EventType

__all__ = ["EventBus", "BaseEvent", "EventType"]
```

- [ ] **Step 2: Create base event types and EventBus**

```python
# events/base.py
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable
import asyncio


class EventType(Enum):
    VOICE_RECEIVED = "voice_received"
    TOOL_EXECUTED = "tool_executed"
    ERROR = "error"
    SYSTEM = "system"


@dataclass
class BaseEvent:
    event_type: EventType
    data: dict = field(default_factory=dict)


class EventBus:
    def __init__(self):
        self._handlers: dict[EventType, list[Callable]] = {}
    
    def register(self, event_type: EventType, handler: Callable):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
    
    async def dispatch(self, event: BaseEvent):
        for handler in self._handlers.get(event.event_type, []):
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
```

- [ ] **Step 3: Verify event bus works**

```python
# Quick test in Python REPL
import asyncio
from events import EventBus, BaseEvent, EventType

bus = EventBus()
results = []

async def handler(e):
    results.append(e.data)

bus.register(EventType.VOICE_RECEIVED, handler)

async def test():
    await bus.dispatch(BaseEvent(EventType.VOICE_RECEIVED, {"text": "hello"}))
    assert results == [{"text": "hello"}]
    print("Event Bus OK")

asyncio.run(test())
```

- [ ] **Step 4: Commit**

```bash
git add events/
git commit -m "feat: add Event Bus with typed events and async dispatch"
```

---

### Task 2: TCP Server Framework

**Covers:** §2.1 Agent Daemon, §2.2 Data Flow

**Files:**
- Create: `agent/__init__.py`
- Create: `agent/daemon.py`

- [ ] **Step 1: Create agent package init**

```python
# agent/__init__.py
from .daemon import TCPServer

__all__ = ["TCPServer"]
```

- [ ] **Step 2: Create TCP server daemon**

```python
# agent/daemon.py
import asyncio
import json
from typing import Callable, Any
from events import EventBus, BaseEvent, EventType


class TCPServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.event_bus = EventBus()
        self._handlers: dict[str, Callable] = {}
    
    def register_handler(self, action: str, handler: Callable):
        self._handlers[action] = handler
    
    async def start(self):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        print(f"TCP Server listening on {addr[0]}:{addr[1]}")
        
        async with server:
            await server.serves_forever()
    
    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        print(f"Client connected: {addr}")
        
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                
                message = json.loads(data.decode().strip())
                action = message.get("action")
                payload = message.get("payload", {})
                
                await self.event_bus.dispatch(
                    BaseEvent(EventType.VOICE_RECEIVED, payload)
                )
                
                if action in self._handlers:
                    result = await self._handlers[action](payload)
                else:
                    result = {"error": f"Unknown action: {action}"}
                
                response = json.dumps(result) + "\n"
                writer.write(response.encode())
                await writer.drain()
        
        except asyncio.IncompleteReadError:
            pass
        finally:
            print(f"Client disconnected: {addr}")
            writer.close()
            await writer.wait_closed()


async def handle_chat(payload: dict) -> dict:
    """Placeholder chat handler"""
    return {
        "code": "",
        "description": f"Received: {payload.get('message', '')}",
        "tool_calls": 0
    }


async def main():
    server = TCPServer()
    server.register_handler("chat", handle_chat)
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Verify TCP server starts**

```bash
python -m agent.daemon
# Should print: TCP Server listening on 127.0.0.1:8765
# Ctrl+C to stop
```

- [ ] **Step 4: Commit**

```bash
git add agent/
git commit -m "feat: add TCP server framework with event dispatch"
```

---

### Task 3: FastAPI HTTP Server

**Covers:** §2.2 Data Flow (HTTP POST /api/chat)

**Files:**
- Create: `server/__init__.py`
- Create: `server/app.py`
- Create: `server/routes.py`

- [ ] **Step 1: Create requirements.txt**

```
fastapi==0.115.0
uvicorn==0.30.0
```

- [ ] **Step 2: Create server package init**

```python
# server/__init__.py
from .app import create_app

__all__ = ["create_app"]
```

- [ ] **Step 3: Create FastAPI app**

```python
# server/app.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="AI Voice Draw", version="0.1.0")
    
    app.include_router(router, prefix="/api")
    
    app.mount("/static", StaticFiles(directory="frontend"), name="static")
    
    @app.get("/")
    async def index():
        return FileResponse("frontend/index.html")
    
    return app
```

- [ ] **Step 4: Create routes**

```python
# server/routes.py
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    canvas_state: Optional[dict] = None


class ChatResponse(BaseModel):
    code: str
    description: str
    tool_calls: int


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # Placeholder - will connect to TCP agent later
    return ChatResponse(
        code="",
        description=f"Received: {request.message}",
        tool_calls=0
    )
```

- [ ] **Step 5: Verify FastAPI starts**

```bash
pip install -r requirements.txt
uvicorn server.app:create_app --factory --reload
# Should print: Uvicorn running on http://127.0.0.1:8000
# Visit http://127.0.0.1:8000/docs for Swagger UI
```

- [ ] **Step 6: Commit**

```bash
git add server/ requirements.txt
git commit -m "feat: add FastAPI HTTP server with chat endpoint"
```

---

### Task 4: Frontend HTML Structure

**Covers:** §1.1 Core Features, §2.1 Frontend

**Files:**
- Create: `frontend/index.html`
- Create: `frontend/css/style.css`

- [ ] **Step 1: Create index.html**

```html
<!-- frontend/index.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Voice Draw</title>
    <link rel="stylesheet" href="/static/css/style.css">
</head>
<body>
    <div class="app-container">
        <header class="app-header">
            <h1>AI Voice Draw</h1>
            <div class="status" id="status">Ready</div>
        </header>
        
        <main class="app-main">
            <div class="canvas-wrapper">
                <canvas id="fabric-canvas" width="800" height="600"></canvas>
            </div>
            
            <aside class="sidebar">
                <div class="voice-controls">
                    <button id="btn-record" class="btn-record">
                        <span class="mic-icon">🎤</span>
                        <span class="btn-text">Start Recording</span>
                    </button>
                    <div class="transcript" id="transcript"></div>
                </div>
                
                <div class="chat-log" id="chat-log">
                    <div class="chat-message system">
                        <p>Say "draw a circle" or "画一个笑脸" to start!</p>
                    </div>
                </div>
                
                <div class="settings">
                    <label>
                        API Key:
                        <input type="password" id="api-key" placeholder="sk-...">
                    </label>
                    <label>
                        Provider:
                        <select id="provider">
                            <option value="openai">OpenAI</option>
                            <option value="claude">Claude</option>
                        </select>
                    </label>
                </div>
            </aside>
        </main>
        
        <footer class="app-footer">
            <button id="btn-undo">Undo</button>
            <button id="btn-redo">Redo</button>
            <button id="btn-clear">Clear</button>
        </footer>
    </div>
    
    <script src="https://cdnjs.cloudflare.com/ajax/libs/fabric.js/5.3.1/fabric.min.js"></script>
    <script src="/static/js/canvas.js"></script>
    <script src="/static/js/speech.js"></script>
    <script src="/static/js/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create style.css**

```css
/* frontend/css/style.css */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #1a1a2e;
    color: #eee;
    height: 100vh;
}

.app-container {
    display: flex;
    flex-direction: column;
    height: 100vh;
}

.app-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1rem 2rem;
    background: #16213e;
    border-bottom: 1px solid #0f3460;
}

.app-header h1 {
    font-size: 1.5rem;
    color: #e94560;
}

.status {
    padding: 0.25rem 0.75rem;
    border-radius: 12px;
    background: #0f3460;
    font-size: 0.875rem;
}

.app-main {
    display: flex;
    flex: 1;
    overflow: hidden;
}

.canvas-wrapper {
    flex: 1;
    display: flex;
    justify-content: center;
    align-items: center;
    background: #16213e;
    padding: 1rem;
}

canvas {
    background: white;
    border-radius: 8px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
}

.sidebar {
    width: 320px;
    background: #16213e;
    border-left: 1px solid #0f3460;
    display: flex;
    flex-direction: column;
    padding: 1rem;
}

.voice-controls {
    margin-bottom: 1rem;
}

.btn-record {
    width: 100%;
    padding: 1rem;
    border: none;
    border-radius: 8px;
    background: #e94560;
    color: white;
    font-size: 1rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    transition: background 0.2s;
}

.btn-record:hover {
    background: #c73e54;
}

.btn-record.recording {
    background: #ff6b6b;
    animation: pulse 1.5s infinite;
}

@keyframes pulse {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.02); }
}

.mic-icon {
    font-size: 1.5rem;
}

.transcript {
    margin-top: 0.5rem;
    padding: 0.75rem;
    background: #0f3460;
    border-radius: 8px;
    min-height: 3rem;
    font-style: italic;
    color: #aaa;
}

.chat-log {
    flex: 1;
    overflow-y: auto;
    margin-bottom: 1rem;
}

.chat-message {
    padding: 0.75rem;
    margin-bottom: 0.5rem;
    border-radius: 8px;
    background: #0f3460;
}

.chat-message.system {
    background: #1a1a40;
    text-align: center;
    font-size: 0.875rem;
    color: #888;
}

.settings {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.settings label {
    font-size: 0.875rem;
    color: #aaa;
}

.settings input,
.settings select {
    width: 100%;
    padding: 0.5rem;
    margin-top: 0.25rem;
    border: 1px solid #0f3460;
    border-radius: 4px;
    background: #1a1a2e;
    color: #eee;
}

.app-footer {
    display: flex;
    gap: 0.5rem;
    padding: 0.75rem 2rem;
    background: #16213e;
    border-top: 1px solid #0f3460;
}

.app-footer button {
    padding: 0.5rem 1rem;
    border: 1px solid #0f3460;
    border-radius: 4px;
    background: #1a1a2e;
    color: #eee;
    cursor: pointer;
    transition: background 0.2s;
}

.app-footer button:hover {
    background: #0f3460;
}
```

- [ ] **Step 3: Verify frontend loads**

```bash
uvicorn server.app:create_app --factory --reload
# Visit http://127.0.0.1:8000
# Should see the AI Voice Draw interface
```

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "feat: add frontend HTML structure and dark theme CSS"
```

---

### Task 5: Frontend JavaScript - Canvas

**Covers:** §3.2 Canvas, §6 Canvas State

**Files:**
- Create: `frontend/js/canvas.js`

- [ ] **Step 1: Create canvas.js**

```javascript
// frontend/js/canvas.js
class CanvasManager {
    constructor(canvasId) {
        this.canvas = new fabric.Canvas(canvasId, {
            backgroundColor: '#ffffff',
            selection: true
        });
        this.history = [];
        this.historyIndex = -1;
        this.maxHistory = 50;
        
        this.canvas.on('object:modified', () => this.saveState());
        this.saveState();
    }
    
    saveState() {
        const json = JSON.stringify(this.canvas.toJSON());
        
        if (this.historyIndex < this.history.length - 1) {
            this.history = this.history.slice(0, this.historyIndex + 1);
        }
        
        this.history.push(json);
        
        if (this.history.length > this.maxHistory) {
            this.history.shift();
        } else {
            this.historyIndex++;
        }
    }
    
    undo() {
        if (this.historyIndex > 0) {
            this.historyIndex--;
            this.canvas.loadFromJSON(this.history[this.historyIndex], () => {
                this.canvas.renderAll();
            });
        }
    }
    
    redo() {
        if (this.historyIndex < this.history.length - 1) {
            this.historyIndex++;
            this.canvas.loadFromJSON(this.history[this.historyIndex], () => {
                this.canvas.renderAll();
            });
        }
    }
    
    clear() {
        this.canvas.clear();
        this.canvas.backgroundColor = '#ffffff';
        this.canvas.renderAll();
        this.saveState();
    }
    
    executeCode(code) {
        try {
            const fn = new Function('canvas', code);
            fn(this.canvas);
            this.canvas.renderAll();
            this.saveState();
            return true;
        } catch (e) {
            console.error('Execute code error:', e);
            return false;
        }
    }
    
    getState() {
        const objects = this.canvas.getObjects().map(obj => ({
            type: obj.type,
            left: Math.round(obj.left),
            top: Math.round(obj.top),
            width: obj.width ? Math.round(obj.width * obj.scaleX) : undefined,
            height: obj.height ? Math.round(obj.height * obj.scaleY) : undefined,
            radius: obj.radius ? Math.round(obj.radius * obj.scaleX) : undefined,
            fill: obj.fill,
            stroke: obj.stroke,
            text: obj.text
        }));
        
        return {
            objects,
            canvas_size: {
                width: this.canvas.getWidth(),
                height: this.canvas.getHeight()
            }
        };
    }
    
    resize(width, height) {
        this.canvas.setDimensions({ width, height });
        this.canvas.renderAll();
    }
}

window.CanvasManager = CanvasManager;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/js/canvas.js
git commit -m "feat: add CanvasManager with undo/redo and state tracking"
```

---

### Task 6: Frontend JavaScript - Speech

**Covers:** §1.1 Core Features, §2.2 Data Flow (STT)

**Files:**
- Create: `frontend/js/speech.js`

- [ ] **Step 1: Create speech.js**

```javascript
// frontend/js/speech.js
class SpeechManager {
    constructor() {
        this.recognition = null;
        this.synthesis = window.speechSynthesis;
        this.isRecording = false;
        this.onResult = null;
        this.onStatusChange = null;
        
        this.initRecognition();
    }
    
    initRecognition() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        
        if (!SpeechRecognition) {
            console.warn('Speech Recognition not supported');
            return;
        }
        
        this.recognition = new SpeechRecognition();
        this.recognition.continuous = false;
        this.recognition.interimResults = true;
        this.recognition.lang = 'zh-CN';
        
        this.recognition.onresult = (event) => {
            const transcript = Array.from(event.results)
                .map(result => result[0].transcript)
                .join('');
            
            if (this.onResult) {
                this.onResult(transcript, event.results[0].isFinal);
            }
        };
        
        this.recognition.onend = () => {
            this.isRecording = false;
            if (this.onStatusChange) {
                this.onStatusChange('idle');
            }
        };
        
        this.recognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            this.isRecording = false;
            if (this.onStatusChange) {
                this.onStatusChange('error', event.error);
            }
        };
    }
    
    start() {
        if (!this.recognition) {
            console.error('Speech Recognition not available');
            return false;
        }
        
        if (this.isRecording) {
            return false;
        }
        
        this.isRecording = true;
        if (this.onStatusChange) {
            this.onStatusChange('recording');
        }
        
        this.recognition.start();
        return true;
    }
    
    stop() {
        if (this.recognition && this.isRecording) {
            this.recognition.stop();
        }
    }
    
    speak(text) {
        return new Promise((resolve) => {
            if (!this.synthesis) {
                resolve();
                return;
            }
            
            this.synthesis.cancel();
            
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.lang = 'zh-CN';
            utterance.rate = 1.0;
            utterance.onend = resolve;
            utterance.onerror = resolve;
            
            this.synthesis.speak(utterance);
        });
    }
    
    speakAsync(text) {
        return this.speak(text);
    }
}

window.SpeechManager = SpeechManager;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/js/speech.js
git commit -m "feat: add SpeechManager with STT and TTS support"
```

---

### Task 7: Frontend JavaScript - App Controller

**Covers:** §2.2 Data Flow, §7 Error Handling

**Files:**
- Create: `frontend/js/app.js`

- [ ] **Step 1: Create app.js**

```javascript
// frontend/js/app.js
class App {
    constructor() {
        this.canvas = new CanvasManager('fabric-canvas');
        this.speech = new SpeechManager();
        this.isProcessing = false;
        
        this.initElements();
        this.initEventListeners();
        this.initSpeechCallbacks();
    }
    
    initElements() {
        this.btnRecord = document.getElementById('btn-record');
        this.btnText = this.btnRecord.querySelector('.btn-text');
        this.transcript = document.getElementById('transcript');
        this.chatLog = document.getElementById('chat-log');
        this.status = document.getElementById('status');
        this.btnUndo = document.getElementById('btn-undo');
        this.btnRedo = document.getElementById('btn-redo');
        this.btnClear = document.getElementById('btn-clear');
        this.apiKeyInput = document.getElementById('api-key');
        this.providerSelect = document.getElementById('provider');
    }
    
    initEventListeners() {
        this.btnRecord.addEventListener('click', () => this.toggleRecording());
        this.btnUndo.addEventListener('click', () => this.canvas.undo());
        this.btnRedo.addEventListener('click', () => this.canvas.redo());
        this.btnClear.addEventListener('click', () => this.canvas.clear());
    }
    
    initSpeechCallbacks() {
        this.speech.onResult = (text, isFinal) => {
            this.transcript.textContent = text;
            
            if (isFinal && text.trim()) {
                this.sendMessage(text.trim());
            }
        };
        
        this.speech.onStatusChange = (status, error) => {
            switch (status) {
                case 'recording':
                    this.btnRecord.classList.add('recording');
                    this.btnText.textContent = 'Stop Recording';
                    this.setStatus('Listening...');
                    break;
                case 'idle':
                    this.btnRecord.classList.remove('recording');
                    this.btnText.textContent = 'Start Recording';
                    this.setStatus('Ready');
                    break;
                case 'error':
                    this.btnRecord.classList.remove('recording');
                    this.btnText.textContent = 'Start Recording';
                    this.setStatus('Error: ' + error);
                    break;
            }
        };
    }
    
    toggleRecording() {
        if (this.speech.isRecording) {
            this.speech.stop();
        } else {
            this.speech.start();
        }
    }
    
    setStatus(text) {
        this.status.textContent = text;
    }
    
    addChatMessage(text, type = 'user') {
        const div = document.createElement('div');
        div.className = `chat-message ${type}`;
        div.innerHTML = `<p>${this.escapeHtml(text)}</p>`;
        this.chatLog.appendChild(div);
        this.chatLog.scrollTop = this.chatLog.scrollHeight;
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    async sendMessage(message) {
        if (this.isProcessing) return;
        
        this.isProcessing = true;
        this.setStatus('Processing...');
        this.addChatMessage(message, 'user');
        
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message,
                    canvas_state: this.canvas.getState()
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data.code) {
                this.canvas.executeCode(data.code);
            }
            
            if (data.description) {
                this.addChatMessage(data.description, 'assistant');
                await this.speech.speak(data.description);
            }
            
        } catch (error) {
            console.error('Send message error:', error);
            this.addChatMessage('Error: ' + error.message, 'system');
            this.setStatus('Error');
        } finally {
            this.isProcessing = false;
            this.setStatus('Ready');
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.app = new App();
});
```

- [ ] **Step 2: Commit**

```bash
git add frontend/js/app.js
git commit -m "feat: add App controller with voice-to-API integration"
```

---

### Task 8: Entry Point and Final Integration

**Covers:** §8 File Structure

**Files:**
- Create: `run.py`

- [ ] **Step 1: Create run.py**

```python
# run.py
import uvicorn
from server.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
```

- [ ] **Step 2: Verify full stack works**

```bash
python run.py
# Visit http://127.0.0.1:8000
# Should see the full AI Voice Draw interface
# Click "Start Recording" to test speech
```

- [ ] **Step 3: Commit**

```bash
git add run.py
git commit -m "feat: add application entry point"
```

---

### Task 9: Merge to Main

**Files:**
- None (merge only)

- [ ] **Step 1: Switch to main and merge**

```bash
git checkout main
git merge stage/s0 --no-ff -m "feat: stage/s0 - TCP framework, Event Bus, and frontend foundation"
git push -u origin main
```

- [ ] **Step 2: Verify**

```bash
git log --oneline -5
# Should show the merge commit
```

# AI 语音绘图工具 — 设计文档

## 1. 项目概述

一款纯语音控制的绘图工具，用户通过自然语言指令完成绘图创作，不使用鼠标或键盘。

### 1.1 核心特性

- **自然语言绘图**：用户说"画一个笑脸"、"在右边画棵树"，系统自动执行
- **实时语音反馈**：每次操作后 TTS 语音确认
- **LLM 驱动**：全部指令通过大模型理解，不做正则匹配
- **可扩展架构**：Tool Registry + 多 Agent 预留

### 1.2 技术选型

| 组件 | 技术 | 理由 |
|------|------|------|
| 语音识别 | Web Speech API (STT) | 浏览器原生，免费，支持中英文 |
| 语音反馈 | Web Speech API (TTS) | 操作确认反馈 |
| 绘图画布 | Fabric.js | 对象化图形管理，支持序列化 |
| 后端 | Python FastAPI | 异步高性能 |
| Agent 通信 | TCP 守护进程 | 长连接，低延迟 |
| LLM | OpenAI / Claude API | 用户自带 API Key |

---

## 2. 架构设计

### 2.1 系统架构图

```
┌──────────────────────────────────────────────────────────┐
│                    Frontend (Browser)                     │
│  ┌────────────┐  ┌────────────┐  ┌───────────────────┐  │
│  │ Web Speech │  │ Fabric.js  │  │ SpeechSynth       │  │
│  │ API (STT)  │──▶ Canvas     │◀──│ (TTS 反馈)        │  │
│  └─────┬──────┘  └────────────┘  └───────────────────┘  │
│        │                                                  │
│        ▼                                                  │
│  ┌─────────────────────────────────┐                     │
│  │   HTTP POST /api/chat           │                     │
│  │   { message, canvas_state }     │                     │
│  └─────────────┬───────────────────┘                     │
└────────────────┼─────────────────────────────────────────┘
                 │ TCP
┌────────────────▼─────────────────────────────────────────┐
│                  Agent Daemon                             │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │                  Event Bus                         │  │
│  │  dispatch(event) → handlers                        │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │              Tool Registry                         │  │
│  │  register_tool(name, handler, schema)              │  │
│  │                                                    │  │
│  │  Built-in Tools:                                   │  │
│  │  ├── draw_circle(center, radius, color, ...)      │  │
│  │  ├── draw_rect(x, y, width, height, color, ...)  │  │
│  │  ├── draw_line(start, end, color, width)          │  │
│  │  ├── draw_text(x, y, text, font_size, color)     │  │
│  │  ├── delete_object(selector)                      │  │
│  │  ├── move_object(selector, x, y)                  │  │
│  │  ├── change_color(selector, color)                │  │
│  │  ├── undo() / redo()                              │  │
│  │  └── clear_canvas()                               │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │              Agent (Main)                          │  │
│  │  - Receives user message                           │  │
│  │  - Calls LLM with tool definitions                 │  │
│  │  - Executes tool calls from LLM response           │  │
│  │  - Returns results to frontend                     │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │              Sub-Agents (Future)                   │  │
│  │  ├── DrawingAgent (handles complex drawings)       │  │
│  │  ├── StyleAgent (handles style/aesthetics)         │  │
│  │  └── AnalysisAgent (analyzes canvas state)         │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │              Provider Layer                        │  │
│  │  class LLMProvider(Protocol):                      │  │
│  │      def chat(messages, tools) -> LLMResponse      │  │
│  │                                                    │  │
│  │  class OpenAIProvider(LLMProvider):                │  │
│  │  class ClaudeProvider(LLMProvider):                │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### 2.2 数据流

1. **用户语音输入** → Web Speech API 识别为文本
2. **前端发送** → POST `/api/chat` { message, canvas_state }
3. **Agent 接收** → Event Bus 分发 `VoiceReceivedEvent`
4. **LLM 调用** → Provider 发送消息 + 工具定义
5. **工具执行** → Tool Registry 执行 LLM 返回的 tool_calls
6. **结果返回** → 前端执行 Fabric.js 代码 + TTS 语音反馈

---

## 3. 核心模块设计

### 3.1 Event Bus

```python
class EventBus:
    def __init__(self):
        self._handlers: dict[EventType, list[Callable]] = {}
    
    def register(self, event_type: EventType, handler: Callable):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
    
    def dispatch(self, event: BaseEvent):
        for handler in self._handlers.get(event.event_type, []):
            handler(event)
```

### 3.2 Tool Registry

```python
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool):
        defn = tool.definition()
        self._tools[defn.name] = tool
        return self
    
    def get_tool_definitions(self) -> list[dict]:
        return [tool.definition().model_dump() for tool in self._tools.values()]
    
    def execute(self, name: str, **kwargs) -> Any:
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[name].execute(**kwargs)
```

### 3.3 Provider 抽象

```python
@dataclass
class ToolCall:
    name: str
    arguments: dict

@dataclass
class LLMResponse:
    content: str
    model: str
    tokens_used: int
    tool_calls: list[ToolCall] = field(default_factory=list)

class LLMProvider(Protocol):
    def chat(self, messages: list[dict], 
             tools: list[dict] | None = None) -> LLMResponse: ...
```

### 3.4 Agent 核心

```python
class DrawingAgent:
    def __init__(self, provider: LLMProvider, registry: ToolRegistry):
        self.provider = provider
        self.registry = registry
        self.event_bus = EventBus()
    
    def chat(self, message: str, canvas_state: dict) -> dict:
        # 1. 构造 LLM 请求
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"画布状态: {canvas_state}\n用户: {message}"}
        ]
        
        # 2. 调用 LLM
        response = self.provider.chat(
            messages=messages,
            tools=self.registry.get_tool_definitions()
        )
        
        # 3. 执行工具调用
        results = []
        for tool_call in response.tool_calls:
            result = self.registry.execute(
                name=tool_call.name,
                **tool_call.arguments
            )
            results.append(result)
        
        # 4. 返回结果
        return {
            "code": "\n".join(r["code"] for r in results),
            "description": "\n".join(r["description"] for r in results),
            "tool_calls": len(results)
        }
```

---

## 4. Tool 定义

### 4.1 绘图工具

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `draw_circle` | center_x, center_y, radius, color | 画圆形 |
| `draw_rect` | x, y, width, height, color | 画矩形 |
| `draw_line` | start_x, start_y, end_x, end_y, color, width | 画线 |
| `draw_text` | x, y, text, font_size, color | 画文字 |
| `draw_ellipse` | center_x, center_y, rx, ry, color | 画椭圆 |

### 4.2 编辑工具

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `delete_object` | selector (type/color/position) | 删除对象 |
| `move_object` | selector, x, y | 移动对象 |
| `change_color` | selector, color | 改变颜色 |
| `resize_object` | selector, scale_x, scale_y | 缩放对象 |

### 4.3 历史工具

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `undo` | 无 | 撤销上一步 |
| `redo` | 无 | 重做 |
| `clear_canvas` | 无 | 清空画布 |

---

## 5. LLM Prompt 设计

```
你是一个绘图助手。用户会用自然语言描述要画的内容。

你需要使用提供的工具来完成用户的绘图请求。

当前画布状态：{canvas_state}

规则：
1. 使用工具来执行绘图操作
2. 如果用户描述复杂（如"画一个笑脸"），拆解为多个简单工具调用
3. 工具调用的参数要合理（坐标、颜色、大小）
4. 如果用户没有指定位置，默认居中
5. 如果用户没有指定颜色，默认使用随机鲜艳颜色
```

---

## 6. 画布状态追踪

每次 LLM 调用前，前端发送当前画布的对象列表：

```json
{
  "objects": [
    {"type": "circle", "left": 100, "top": 100, "radius": 50, "fill": "red"},
    {"type": "rect", "left": 200, "top": 150, "width": 80, "height": 60, "fill": "blue"}
  ],
  "canvas_size": {"width": 800, "height": 600}
}
```

---

## 7. 错误处理

| 场景 | 处理方式 |
|------|----------|
| LLM 返回无效工具调用 | 返回错误信息，语音提示"指令执行失败" |
| 语音识别错误 | 显示识别文本，用户可重试 |
| API Key 无效 | 引导用户在设置面板重新输入 |
| 网络断开 | 返回错误，前端提示重试 |
| 工具执行失败 | 捕获异常，返回错误详情 |

---

## 8. 文件结构

```
voice-draw/
├── agent/
│   ├── __init__.py
│   ├── daemon.py           # Agent 守护进程 (TCP server)
│   ├── main.py             # DrawingAgent 核心
│   └── event_bus.py        # Event Bus
├── tools/
│   ├── __init__.py
│   ├── base.py             # BaseTool + ToolDefinition
│   ├── registry.py         # ToolRegistry
│   ├── drawing/            # 绘图工具
│   │   ├── circle.py
│   │   ├── rect.py
│   │   ├── line.py
│   │   └── text.py
│   ├── editing/            # 编辑工具
│   │   ├── delete.py
│   │   ├── move.py
│   │   └── color.py
│   └── history/            # 历史工具
│       ├── undo.py
│       └── redo.py
├── providers/
│   ├── __init__.py
│   ├── base.py             # LLMProvider Protocol + LLMResponse
│   ├── openai_provider.py
│   └── claude_provider.py
├── events/
│   ├── __init__.py
│   └── base.py             # BaseEvent + 事件类型
├── server/
│   ├── __init__.py
│   ├── app.py              # FastAPI 前端接口
│   └── routes.py
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── app.js
│       ├── speech.js       # Web Speech API
│       ├── canvas.js       # Fabric.js
│       └── tts.js          # 语音反馈
├── requirements.txt
└── README.md
```

---

## 9. 实现计划

### Phase 1: 基础框架
- [ ] Event Bus 实现
- [ ] BaseEvent 定义
- [ ] ToolRegistry 实现
- [ ] BaseTool 抽象

### Phase 2: Agent 核心
- [ ] DrawingAgent 实现
- [ ] Provider 抽象层
- [ ] OpenAI Provider 实现
- [ ] Agent 守护进程 (TCP)

### Phase 3: 工具实现
- [ ] 绘图工具 (circle, rect, line, text)
- [ ] 编辑工具 (delete, move, color)
- [ ] 历史工具 (undo, redo, clear)

### Phase 4: 前端
- [ ] Web Speech API 集成
- [ ] Fabric.js 画布
- [ ] TTS 语音反馈
- [ ] API 通信

### Phase 5: 集成测试
- [ ] 端到端测试
- [ ] 错误处理
- [ ] 性能优化

---

## 10. 成本控制

| 策略 | 说明 |
|------|------|
| 用户自带 API Key | 零运营成本 |
| 工具调用而非代码生成 | 减少 token 消耗 |
| 简单指令快速处理 | 减少 LLM 调用次数 |
| 画布状态压缩 | 只发送必要信息 |

---

## 11. 扩展性

### 11.1 新增工具
只需实现 `BaseTool` 接口并注册到 `ToolRegistry`：

```python
class NewTool(BaseTool):
    def definition(self) -> ToolDefinition: ...
    def execute(self, **kwargs) -> Any: ...

registry.register(NewTool())
```

### 11.2 新增 Provider
只需实现 `LLMProvider` Protocol：

```python
class NewProvider:
    def chat(self, messages, tools) -> LLMResponse: ...
```

### 11.3 多 Agent
未来可拆分为：
- DrawingAgent：负责绘图
- StyleAgent：负责风格
- AnalysisAgent：负责分析画布

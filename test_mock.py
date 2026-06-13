from server.app import create_app
from server.routes import _runner
from agent.runner import AgentResponse


async def mock_run(message, canvas_state=None):
    msg = message.lower()
    if "circle" in msg or "圆" in msg:
        return AgentResponse(
            content="画了一个红色圆圈",
            code="const c = new fabric.Circle({left:300,top:200,radius:60,fill:'red'});canvas.add(c);canvas.renderAll();",
            description="已画红色圆圈",
            tool_calls=[{"name": "draw_circle", "arguments": {}, "is_error": False}],
            rounds=1,
        )
    elif "rect" in msg or "方" in msg:
        return AgentResponse(
            content="画了一个蓝色矩形",
            code="const r = new fabric.Rect({left:200,top:150,width:120,height:80,fill:'blue'});canvas.add(r);canvas.renderAll();",
            description="已画蓝色矩形",
            tool_calls=[{"name": "draw_rect", "arguments": {}, "is_error": False}],
            rounds=1,
        )
    elif "hello" in msg or "你好" in msg:
        return AgentResponse(content="你好！请告诉我你想画什么", rounds=1)
    else:
        return AgentResponse(
            content="收到: " + message,
            code="const c = new fabric.Circle({left:350,top:250,radius:40,fill:'#4ECDC4'});canvas.add(c);canvas.renderAll();",
            description="默认画了一个青色圆圈",
            tool_calls=[{"name": "draw_circle", "arguments": {}, "is_error": False}],
            rounds=1,
        )


_runner.run = mock_run

from fastapi.testclient import TestClient

app = create_app()
client = TestClient(app)

tests = [
    "hello",
    "draw a circle",
    "draw a rectangle",
    "你好",
    "画一个圆",
    "画一个矩形",
]

for msg in tests:
    r = client.post("/api/chat", json={"message": msg})
    d = r.json()
    print(f"[{msg}]")
    print(f"  content: {d['content']}")
    code = d.get("code", "")
    print(f"  code: {code[:60]}..." if len(code) > 60 else f"  code: {code}")
    print(f"  description: {d['description']}")
    print(f"  tool_calls: {d['tool_calls']}  rounds: {d['rounds']}  success: {d['success']}")
    print()

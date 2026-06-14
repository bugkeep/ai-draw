from server.app import create_app
from fastapi.testclient import TestClient
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
    return AgentResponse(content="收到: " + message, rounds=1)


_runner.run = mock_run

app = create_app()
client = TestClient(app)

# Test frontend
r = client.get("/")
print("=== Frontend ===")
print("Status:", r.status_code)
print("Title:", "AI Voice Draw" in r.text)

# Test API
r2 = client.post("/api/chat", json={"message": "画一个圆"})
print()
print("=== API ===")
print("Status:", r2.status_code)
d = r2.json()
print("run_id:", d["run_id"])
print("code:", d["code"][:50])
print("success:", d["success"])

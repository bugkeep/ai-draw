import uvicorn
from server.app import create_app
from server.routes import _runner
from agent.runner import AgentResponse


original_run = _runner.run


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
    elif "rect" in msg or "方" in msg or "矩形" in msg:
        return AgentResponse(
            content="画了一个蓝色矩形",
            code="const r = new fabric.Rect({left:200,top:150,width:120,height:80,fill:'blue'});canvas.add(r);canvas.renderAll();",
            description="已画蓝色矩形",
            tool_calls=[{"name": "draw_rect", "arguments": {}, "is_error": False}],
            rounds=1,
        )
    elif "line" in msg or "线" in msg:
        return AgentResponse(
            content="画了一条绿线",
            code="const l = new fabric.Line([100,100,400,300],{stroke:'green',strokeWidth:3});canvas.add(l);canvas.renderAll();",
            description="已画绿线",
            tool_calls=[{"name": "draw_line", "arguments": {}, "is_error": False}],
            rounds=1,
        )
    elif "text" in msg or "文字" in msg or "文本" in msg:
        return AgentResponse(
            content="添加了文字",
            code="const t = new fabric.Text('Hello AI Draw',{left:150,top:250,fontSize:32,fill:'#e94560'});canvas.add(t);canvas.renderAll();",
            description="已添加文字 Hello AI Draw",
            tool_calls=[{"name": "draw_text", "arguments": {}, "is_error": False}],
            rounds=1,
        )
    elif "clear" in msg or "清空" in msg:
        return AgentResponse(
            content="已清空画布",
            code="canvas.clear();canvas.backgroundColor='#ffffff';canvas.renderAll();",
            description="画布已清空",
            tool_calls=[{"name": "clear_canvas", "arguments": {}, "is_error": False}],
            rounds=1,
        )
    elif "hello" in msg or "你好" in msg:
        return AgentResponse(
            content="你好！我是 AI 绘图助手，请告诉我你想画什么。可以说：画一个圆、画一个矩形、画一条线、添加文字等。",
            rounds=1,
        )
    else:
        return AgentResponse(
            content=f"收到指令: {message}",
            code="const c = new fabric.Circle({left:350,top:250,radius:40,fill:'#4ECDC4'});canvas.add(c);canvas.renderAll();",
            description="默认画了一个青色圆圈",
            tool_calls=[{"name": "draw_circle", "arguments": {}, "is_error": False}],
            rounds=1,
        )


_runner.run = mock_run

app = create_app()
print("Server starting at http://localhost:8000")
uvicorn.run(app, host="0.0.0.0", port=8000)

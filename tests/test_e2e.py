from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from server.app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.state.daemon_client.send = AsyncMock(return_value={
        "run_id": "test-run",
        "content": "Drawing complete",
        "code": "{ const circle = new fabric.Circle({radius: 50}); canvas.add(circle); }",
        "description": "Drew a circle",
        "tool_calls": 1,
        "success": True,
        "error": "",
        "rounds": 2,
        "latency_ms": 25.0,
    })
    return TestClient(app), app.state.daemon_client.send


def test_index_serves_frontend(client):
    http, _ = client
    response = http.get("/")
    assert response.status_code == 200


def test_chat_endpoint_forwards_voice_context(client):
    http, daemon_send = client
    history = [{"role": "user", "content": "画一棵树"}]

    response = http.post("/api/chat", json={
        "message": "在旁边画一座房子",
        "canvas_state": {"objects": []},
        "history": history,
        "provider": "openai",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["tool_calls"] == 1
    assert data["latency_ms"] == 25.0
    daemon_send.assert_awaited_once()
    assert daemon_send.await_args.args[1]["history"] == history

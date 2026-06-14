import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from providers.base import LLMResponse, ToolCall
from server.app import create_app
from server.routes import _runner


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


class TestHealthCheck:
    def test_index(self, client):
        response = client.get("/")
        assert response.status_code == 200


class TestChatEndpoint:
    @patch.object(_runner, "run", new_callable=AsyncMock)
    def test_simple_message_no_tools(self, mock_run, client):
        from agent.runner import AgentResponse
        mock_run.return_value = AgentResponse(content="I can help you draw!")

        response = client.post("/api/chat", json={"message": "hello"})
        assert response.status_code == 200
        data = response.json()
        assert data["content"] == "I can help you draw!"
        assert data["code"] == ""
        assert data["success"] is True

    @patch.object(_runner, "run", new_callable=AsyncMock)
    def test_draw_circle(self, mock_run, client):
        from agent.runner import AgentResponse
        mock_run.return_value = AgentResponse(
            content="Drawing a red circle",
            code="const circle = new fabric.Circle({radius: 50, fill: 'red'});",
            description="Drew a red circle",
            tool_calls=[{"name": "draw_circle", "arguments": {}, "is_error": False}],
        )

        response = client.post("/api/chat", json={"message": "draw a red circle"})
        assert response.status_code == 200
        data = response.json()
        assert "fabric.Circle" in data["code"]
        assert data["tool_calls"] == 1
        assert data["success"] is True

    @patch.object(_runner, "run", new_callable=AsyncMock)
    def test_draw_multiple_shapes(self, mock_run, client):
        from agent.runner import AgentResponse
        mock_run.return_value = AgentResponse(
            content="Drawing shapes",
            code="const circle = new fabric.Circle(); const rect = new fabric.Rect();",
            description="Drew circle and rect",
            tool_calls=[
                {"name": "draw_circle", "arguments": {}, "is_error": False},
                {"name": "draw_rect", "arguments": {}, "is_error": False},
            ],
        )

        response = client.post("/api/chat", json={"message": "draw a circle and a rectangle"})
        assert response.status_code == 200
        data = response.json()
        assert "fabric.Circle" in data["code"]
        assert "fabric.Rect" in data["code"]
        assert data["tool_calls"] == 2

    @patch.object(_runner, "run", new_callable=AsyncMock)
    def test_with_canvas_state(self, mock_run, client):
        from agent.runner import AgentResponse
        mock_run.return_value = AgentResponse(content="Canvas has a red circle")

        canvas_state = {
            "objects": [
                {"type": "circle", "left": 100, "top": 100, "fill": "red"},
            ]
        }
        response = client.post("/api/chat", json={
            "message": "what's on the canvas",
            "canvas_state": canvas_state,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_run.assert_called_once_with(
            message="what's on the canvas",
            canvas_state=canvas_state,
        )

    @patch.object(_runner, "run", new_callable=AsyncMock)
    def test_llm_error(self, mock_run, client):
        from agent.runner import AgentResponse
        mock_run.return_value = AgentResponse(
            success=False,
            error="LLM call failed: API error",
        )

        response = client.post("/api/chat", json={"message": "draw"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "API error" in data["error"]

    @patch.object(_runner, "run", new_callable=AsyncMock)
    def test_tool_failure_continues(self, mock_run, client):
        from agent.runner import AgentResponse
        mock_run.return_value = AgentResponse(
            content="Tried something",
            tool_calls=[{"name": "nonexistent_tool", "arguments": {}, "is_error": True}],
        )

        response = client.post("/api/chat", json={"message": "do something"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestMultiRoundConversation:
    @patch.object(_runner, "run", new_callable=AsyncMock)
    def test_two_round_tool_calls(self, mock_run, client):
        from agent.runner import AgentResponse
        mock_run.return_value = AgentResponse(
            content="All done",
            code="const circle = new fabric.Circle(); const rect = new fabric.Rect();",
            description="Drew circle and rect",
            tool_calls=[
                {"name": "draw_circle", "arguments": {}, "is_error": False},
                {"name": "draw_rect", "arguments": {}, "is_error": False},
            ],
        )

        response = client.post("/api/chat", json={"message": "draw a circle then a rect"})
        assert response.status_code == 200
        data = response.json()
        assert data["tool_calls"] == 2
        assert "fabric.Circle" in data["code"]
        assert "fabric.Rect" in data["code"]

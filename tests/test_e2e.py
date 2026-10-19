from fastapi.testclient import TestClient

from server.app import create_app


def test_frontend_and_current_api(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAW_AGENT_DB", str(tmp_path / "app.sqlite3"))
    with TestClient(create_app()) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Draw Agent Studio" in page.text
        session = client.post("/api/studio/sessions", json={"title": "端到端"})
        assert session.status_code == 200
        session_id = session.json()["id"]
        state = client.get(f"/api/studio/sessions/{session_id}")
        assert state.json()["document"]["elements"] == []
        assert client.get("/api/chat").status_code == 404

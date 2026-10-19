import time

from fastapi.testclient import TestClient

from server.app import create_app
from studio.store import StudioStore


def test_rename_preserves_canvas_history_messages_and_other_sessions(tmp_path, monkeypatch):
    path = tmp_path / "sessions.sqlite3"
    monkeypatch.setenv("DRAW_AGENT_DB", str(path))
    with TestClient(create_app()) as client:
        first = client.post("/api/studio/sessions", json={"title": "新的画布"}).json()
        second = client.post("/api/studio/sessions", json={"title": "另一张画布"}).json()
        task = client.post(f"/api/studio/sessions/{first['id']}/messages", json={"message": "画一个蓝色圆形"}).json()
        for _ in range(100):
            status = client.get(f"/api/studio/tasks/{task['id']}").json()["status"]
            if status == "completed":
                break
            time.sleep(0.01)
        assert status == "completed"
        before = client.get(f"/api/studio/sessions/{first['id']}").json()
        document_id = before["document"]["id"]
        with client.websocket_connect(f"/api/studio/documents/{document_id}/events?after={before['event_seq']}") as socket:
            response = client.patch(f"/api/studio/sessions/{first['id']}", json={"title": "  公园设计  "})
            assert response.status_code == 200
            assert response.json() == {"id": first["id"], "document_id": document_id, "title": "公园设计"}
            event = socket.receive_json()
            assert event["type"] == "session" and event["document_id"] == document_id
            assert event["payload"]["id"] == first["id"]
        after = client.get(f"/api/studio/sessions/{first['id']}").json()
        assert after["title"] == after["document"]["title"] == "公园设计"
        for key in ("id", "version", "elements", "selection"):
            assert before["document"][key] == after["document"][key]
        assert before["messages"] == after["messages"] and before["tasks"] == after["tasks"]
        assert client.get(f"/api/studio/sessions/{second['id']}").json() == second
        for title in ("", " \t\n ", "名" * 121):
            assert client.patch(f"/api/studio/sessions/{first['id']}", json={"title": title}).status_code == 422
        assert client.patch("/api/studio/sessions/missing", json={"title": "名称"}).status_code == 404
        assert client.post(f"/api/studio/documents/{document_id}/history", json={"direction": "undo"}).json()["elements"] == []
        assert len(client.post(f"/api/studio/documents/{document_id}/history", json={"direction": "redo"}).json()["elements"]) == 1
    reopened = StudioStore(path)
    assert reopened.get_session(first["id"])["title"] == "公园设计"
    assert len(reopened.get_document(document_id)["elements"]) == 1
    reopened.close()

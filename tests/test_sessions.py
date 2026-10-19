import time

import pytest
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


def test_delete_session_removes_owned_data_and_notifies_viewers(tmp_path, monkeypatch):
    path = tmp_path / "sessions.sqlite3"
    monkeypatch.setenv("DRAW_AGENT_DB", str(path))
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/studio/health").json()["api_revision"] == 7
        first = client.post("/api/studio/sessions", json={"title": "要删除的画布"}).json()
        second = client.post("/api/studio/sessions", json={"title": "保留的画布"}).json()
        task = client.post(f"/api/studio/sessions/{first['id']}/messages", json={"message": "画一个蓝色圆形"}).json()
        for _ in range(100):
            state = client.get(f"/api/studio/tasks/{task['id']}").json()
            if state["status"] == "completed":
                break
            time.sleep(0.01)
        assert state["status"] == "completed"
        before = client.get(f"/api/studio/sessions/{first['id']}").json()
        assert client.get(f"/api/studio/tasks/{task['id']}/logs").json()
        document_id = before["document"]["id"]
        with client.websocket_connect(f"/api/studio/documents/{document_id}/events?after={before['event_seq']}") as socket:
            response = client.delete(f"/api/studio/sessions/{first['id']}")
            assert response.status_code == 200
            assert response.json() == {"id": first["id"], "document_id": document_id}
            event = socket.receive_json()
            assert event["type"] == "session_deleted" and event["seq"] > before["event_seq"]
            assert event["payload"] == response.json()
        assert client.get(f"/api/studio/sessions/{first['id']}").status_code == 404
        assert client.get(f"/api/studio/tasks/{task['id']}").status_code == 404
        assert client.get(f"/api/studio/sessions/{second['id']}").json() == second
        assert [item["id"] for item in client.get("/api/studio/sessions").json()] == [second["id"]]
        assert client.delete(f"/api/studio/sessions/{first['id']}").status_code == 404
        connection = app.state.studio_store.connection
        for table in ("messages", "tasks", "tool_logs", "events"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    reopened = StudioStore(path)
    with pytest.raises(KeyError):
        reopened.get_document(document_id)
    assert reopened.get_session(second["id"]) == second
    reopened.close()


def test_delete_rejects_running_and_queued_tasks_in_only_its_session(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAW_AGENT_DB", str(tmp_path / "sessions.sqlite3"))
    with TestClient(create_app()) as client:
        first = client.post("/api/studio/sessions", json={"title": "绘图中"}).json()
        other = client.post("/api/studio/sessions", json={"title": "空画布"}).json()
        first_task = client.post(f"/api/studio/sessions/{first['id']}/messages", json={"message": "用画笔画一张笑脸"}).json()
        queued = client.post(f"/api/studio/sessions/{first['id']}/messages", json={"message": "画一个蓝色圆形"}).json()
        response = client.delete(f"/api/studio/sessions/{first['id']}")
        assert response.status_code == 409 and "停止" in response.json()["detail"]
        assert client.delete(f"/api/studio/sessions/{other['id']}").status_code == 200
        for task in (queued, first_task):
            assert client.post(f"/api/studio/tasks/{task['id']}/cancel").status_code == 200
        for _ in range(300):
            statuses = [client.get(f"/api/studio/tasks/{task['id']}").json()["status"] for task in (first_task, queued)]
            if all(status == "cancelled" for status in statuses):
                break
            time.sleep(0.01)
        assert statuses == ["cancelled", "cancelled"]
        assert client.delete(f"/api/studio/sessions/{first['id']}").status_code == 200

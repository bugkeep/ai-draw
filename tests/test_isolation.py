import time

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from studio.store import StudioStore


def test_task_context_excludes_other_sessions_and_future_queued_requests(tmp_path):
    store = StudioStore(tmp_path / "isolation.sqlite3")
    first = store.create_session("A")["id"]
    second = store.create_session("B")["id"]
    task_one = store.create_task(first)["id"]
    store.add_message(first, "user", "A 的当前指令", task_one)
    task_two = store.create_task(first)["id"]
    store.add_message(first, "user", "A 的后续排队指令", task_two)
    foreign_task = store.create_task(second)["id"]
    store.add_message(second, "user", "B 的独立指令", foreign_task)
    store.add_message(first, "assistant", "A 当前任务的结果", task_one)
    current = store.context_messages(first, task_one)
    assert [item["content"] for item in current] == ["A 的当前指令", "A 当前任务的结果"]
    queued = store.context_messages(first, task_two)
    assert [item["content"] for item in queued] == ["A 的当前指令", "A 当前任务的结果", "A 的后续排队指令"]
    with pytest.raises(KeyError):
        store.context_messages(first, foreign_task)
    store.close()


def test_parallel_sessions_keep_scenes_messages_tasks_and_events_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAW_AGENT_DB", str(tmp_path / "sessions.sqlite3"))
    with TestClient(create_app()) as client:
        first = client.post("/api/studio/sessions", json={"title": "A"}).json()
        second = client.post("/api/studio/sessions", json={"title": "B"}).json()
        one = client.post(f"/api/studio/sessions/{first['id']}/messages", json={"message": "用画笔画一张笑脸"}).json()["id"]
        two = client.post(f"/api/studio/sessions/{second['id']}/messages", json={"message": "画一个蓝色圆形"}).json()["id"]
        for _ in range(150):
            statuses = [client.get(f"/api/studio/tasks/{task_id}").json()["status"] for task_id in (one, two)]
            if statuses == ["completed", "completed"]:
                break
            time.sleep(0.02)
        assert statuses == ["completed", "completed"]
        scene_a = client.get(f"/api/studio/sessions/{first['id']}").json()
        scene_b = client.get(f"/api/studio/sessions/{second['id']}").json()
        assert len(scene_a["document"]["elements"]) == 4
        assert len(scene_b["document"]["elements"]) == 1
        assert {item["task_id"] for item in scene_a["messages"]} == {one}
        assert {item["task_id"] for item in scene_b["messages"]} == {two}
        assert {item["id"] for item in scene_a["tasks"]} == {one}
        assert {item["id"] for item in scene_b["tasks"]} == {two}
        store = client.app.state.studio_store
        for document in (first["document"], second["document"]):
            assert all(event["document_id"] == document["id"] for event in store.events_after(document["id"], 0))
        before = scene_b["document"]
        client.post(f"/api/studio/documents/{first['document']['id']}/history", json={"direction": "undo"})
        assert client.get(f"/api/studio/sessions/{second['id']}").json()["document"] == before

from fastapi.testclient import TestClient
import pytest
import time

from server.app import create_app
from studio.actions import apply_action, create_native_element, element_fingerprint, validate_action
from studio.fast_path import fast_actions
from studio.store import StudioStore, VersionConflict


def test_create_move_select_and_history(tmp_path):
    store = StudioStore(tmp_path / "studio.sqlite3")
    session = store.create_session("示例")
    document_id = session["document"]["id"]
    created = store.commit_action(document_id, {
        "kind": "create_element",
        "args": {"type": "rectangle", "x": 20, "y": 30, "width": 100, "height": 60, "name": "卡片"},
    }, {})["document"]
    element_id = created["elements"][0]["id"]
    moved = store.commit_action(document_id, {
        "kind": "move_elements", "args": {"ids": [element_id], "dx": 40, "dy": -10},
    }, {element_id: 1})["document"]
    assert moved["elements"][0]["x"] == 60
    assert moved["elements"][0]["y"] == 20
    assert moved["selection"] == [element_id]
    assert store.history_step(document_id, "undo")["elements"][0]["x"] == 20
    assert store.history_step(document_id, "redo")["elements"][0]["x"] == 60


def test_disjoint_agent_actions_and_conflicts(tmp_path):
    store = StudioStore(tmp_path / "studio.sqlite3")
    document_id = store.create_session()["document"]["id"]
    first = store.commit_action(document_id, {"kind": "create_element", "args": {"type": "ellipse"}}, {})["changed_ids"][0]
    second = store.commit_action(document_id, {"kind": "create_element", "args": {"type": "rectangle"}}, {})["changed_ids"][0]
    store.commit_action(document_id, {"kind": "move_elements", "args": {"ids": [first], "dx": 10, "dy": 0}}, {first: 1})
    store.commit_action(document_id, {"kind": "move_elements", "args": {"ids": [second], "dx": 20, "dy": 0}}, {second: 1})
    with pytest.raises(VersionConflict):
        store.commit_action(document_id, {"kind": "move_elements", "args": {"ids": [first], "dx": 5, "dy": 0}}, {first: 1})


def test_freehand_element_is_native_and_svg_is_rejected():
    element = create_native_element({
        "type": "freedraw", "points": [(10, 10), (20, 30), (40, 25)], "name": "轮廓",
    })
    assert element["type"] == "freedraw"
    assert element["points"] == [[0, 0], [10, 20], [30, 15]]
    assert len(element["pressures"]) == 3
    with pytest.raises(ValueError):
        validate_action({"kind": "create_element", "args": {"type": "svg"}})
    ray = create_native_element({"type": "line", "x": 465, "y": 40,
                                 "points": [[0, 0], [0, 25]], "name": "太阳光芒"})
    assert ray["x"] == 465 and ray["y"] == 40
    assert ray["points"] == [[0, 0], [0, 25]]
    absolute = create_native_element({"type": "line", "x": 465, "y": 40,
                                      "pointSpace": "absolute", "points": [[10, 15], [35, 15]]})
    assert absolute["x"] == 10 and absolute["y"] == 15
    with pytest.raises(ValueError):
        validate_action({"kind": "update_elements", "args": {
            "ids": ["example"], "patch": {"opacity": 120},
        }})
    with pytest.raises(ValueError):
        create_native_element({"type": "line", "x": 99999, "y": 1,
                               "points": [[0, 0], [50, 0]]})


def test_http_session_snapshot_and_websocket(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAW_AGENT_DB", str(tmp_path / "http.sqlite3"))
    app = create_app()
    with TestClient(app) as client:
        session = client.post("/api/studio/sessions", json={"title": "测试画布"}).json()
        document_id = session["document"]["id"]
        native = create_native_element({"type": "rectangle", "x": 20, "y": 30})
        response = client.put(f"/api/studio/documents/{document_id}/scene", json={
            "version": 0, "elements": [native], "selection": [native["id"]], "client_id": "test-client",
        })
        assert response.status_code == 200
        assert response.json()["version"] == 1
        assert client.put(f"/api/studio/documents/{document_id}/scene", json={
            "version": 0, "elements": [], "selection": [],
        }).status_code == 409
        with client.websocket_connect(f"/api/studio/documents/{document_id}/events?after=0") as websocket:
            event = websocket.receive_json()
            assert event["type"] == "scene"
            assert event["payload"]["document"]["elements"][0]["id"] == native["id"]
        undo = client.post(f"/api/studio/documents/{document_id}/history", json={"direction": "undo"})
        assert undo.status_code == 200
        assert undo.json()["elements"] == []


def test_tool_logs_stay_in_sqlite(tmp_path):
    store = StudioStore(tmp_path / "studio.sqlite3")
    session = store.create_session()
    task = store.create_task(session["id"])
    store.log_tool(task["id"], "child:face", 1, "create_element", {"result": {"ok": True}})
    assert store.tool_logs(task["id"])[0]["payload"] == {"result": {"ok": True}}
    assert "tool_logs" not in store.get_session(session["id"])


def test_contextual_fast_actions_prepare_real_canvas_operations():
    element = create_native_element({"type": "ellipse", "x": 100, "y": 80, "name": "主圆"})
    document = {"elements": [element], "selection": [element["id"]]}
    actions, _ = fast_actions("把它向右移动 80 像素", document)
    moved, _, _ = apply_action(document["elements"], document["selection"], actions[0])
    assert moved[0]["x"] == 180
    selected, _ = fast_actions("选中刚才的圆形", document)
    assert selected[0]["args"]["ids"] == [element["id"]]
    pen, _ = fast_actions("用画笔画一张笑脸", document)
    assert len(pen) == 4
    assert all(item["args"]["type"] == "freedraw" for item in pen)
    created, _ = fast_actions("画一个蓝色圆形，名字叫主圆。", {"elements": [], "selection": []})
    assert created[0]["args"]["type"] == "ellipse"
    assert created[0]["args"]["name"] == "主圆"
    assert fast_actions("把它向右移动 80 像素，再改成红色", document) is None
    assert fast_actions("不要向右移动 80 像素", document) is None
    assert fast_actions("用画笔画一张笑脸，再画一棵树", document) is None


def test_real_http_task_edits_the_same_document(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAW_AGENT_DB", str(tmp_path / "tasks.sqlite3"))
    with TestClient(create_app()) as client:
        session = client.post("/api/studio/sessions", json={"title": "连续编辑"}).json()
        session_id = session["id"]
        for prompt in ("画一个蓝色圆形，名字叫主圆。", "把它向右移动 80 像素。"):
            task_id = client.post(f"/api/studio/sessions/{session_id}/messages", json={"message": prompt}).json()["id"]
            for _ in range(100):
                task = client.get(f"/api/studio/tasks/{task_id}").json()
                if task["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
            assert task["status"] == "completed", task["error"]
        loaded = client.get(f"/api/studio/sessions/{session_id}").json()
        assert len(loaded["messages"]) == 4
        assert loaded["document"]["elements"][0]["x"] == 340
        assert loaded["document"]["elements"][0]["customData"]["name"] == "主圆"


def test_metadata_normalization_does_not_add_undo_entry(tmp_path):
    store = StudioStore(tmp_path / "studio.sqlite3")
    document_id = store.create_session()["document"]["id"]
    created = store.commit_action(document_id, {"kind": "create_element", "args": {"type": "ellipse"}}, {})["document"]
    element = {**created["elements"][0], "index": "a0", "version": 2, "versionNonce": 12}
    normalized = store.save_snapshot(document_id, created["version"], [element], created["selection"])
    assert normalized["version"] == created["version"]
    assert store.history_step(document_id, "undo")["elements"] == []


def test_agent_edit_survives_editor_metadata_normalization(tmp_path):
    store = StudioStore(tmp_path / "studio.sqlite3")
    document_id = store.create_session()["document"]["id"]
    created = store.commit_action(document_id, {"kind": "create_element", "args": {"type": "rectangle"}}, {})["document"]
    element = created["elements"][0]
    expected = {element["id"]: {"version": element["version"], "fingerprint": element_fingerprint(element)}}
    normalized = {**element, "index": "a0", "version": element["version"] + 1, "versionNonce": 99}
    store.save_snapshot(document_id, created["version"], [normalized], created["selection"])
    moved = store.commit_action(document_id, {
        "kind": "move_elements", "args": {"ids": [element["id"]], "dx": 25, "dy": 0},
    }, expected)["document"]
    assert moved["elements"][0]["x"] == element["x"] + 25


def test_content_checks_allow_equivalent_browser_numbers_and_reject_changed_geometry(tmp_path):
    store = StudioStore(tmp_path / "content.sqlite3")
    document_id = store.create_session()["document"]["id"]
    document = store.commit_action(document_id, {"kind": "create_element", "args": {
        "type": "rectangle", "x": 300.0, "y": 120.0, "width": 80.0, "height": 60.0,
    }}, {})["document"]
    element = document["elements"][0]
    expected = {element["id"]: {"fingerprint": element_fingerprint(element)}}
    normalized = {**element, **{key: int(element[key]) for key in ("x", "y", "width", "height", "strokeWidth")},
                  "boundElements": []}
    assert element_fingerprint(normalized) == element_fingerprint(element)
    normalized_document = store.save_snapshot(document_id, document["version"], [normalized], document["selection"])
    assert normalized_document["version"] == document["version"]
    moved = store.commit_action(document_id, {"kind": "move_elements", "args": {
        "ids": [element["id"]], "dx": 20, "dy": 0,
    }}, expected)["document"]
    assert moved["elements"][0]["x"] == 320
    with pytest.raises(VersionConflict):
        store.commit_action(document_id, {"kind": "move_elements", "args": {
            "ids": [element["id"]], "dx": 20, "dy": 0,
        }}, expected)
    legacy = {element["id"]: {"fingerprint": element_fingerprint(moved["elements"][0], legacy=True)}}
    updated = store.commit_action(document_id, {"kind": "move_elements", "args": {
        "ids": [element["id"]], "dx": 5, "dy": 0,
    }}, legacy)["document"]
    assert updated["elements"][0]["x"] == 325
    store.close()


def test_large_canvas_can_continue_editing(tmp_path):
    store = StudioStore(tmp_path / "large.sqlite3")
    document_id = store.create_session()["document"]["id"]
    elements = [create_native_element({"type": "rectangle", "x": (index % 80) * 20,
                                      "y": (index // 80) * 20, "width": 16, "height": 16})
                for index in range(3100)]
    document = store.save_snapshot(document_id, 0, elements, [])
    updated = store.commit_action(document_id, {"kind": "move_elements", "args": {
        "ids": [elements[-1]["id"]], "dx": 5, "dy": 0,
    }}, {})["document"]
    assert len(updated["elements"]) == 3100 and updated["elements"][-1]["x"] == elements[-1]["x"] + 5
    assert store.history_step(document_id, "undo")["elements"] == document["elements"]
    store.close()


def test_websocket_replays_more_than_one_event_page(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAW_AGENT_DB", str(tmp_path / "events.sqlite3"))
    with TestClient(create_app()) as client:
        document_id = client.post("/api/studio/sessions", json={}).json()["document"]["id"]
        store = client.app.state.studio_store
        for index in range(503):
            store.publish(document_id, "progress", {"step": index})
        with client.websocket_connect(f"/api/studio/documents/{document_id}/events?after=0") as websocket:
            events = [websocket.receive_json() for _ in range(503)]
        assert [event["payload"]["step"] for event in events] == list(range(503))
        for _ in range(50):
            if not store.listeners.get(document_id):
                break
            time.sleep(0.01)
        assert not store.listeners.get(document_id)


def test_queued_task_can_be_cancelled_without_modifying_canvas(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAW_AGENT_DB", str(tmp_path / "cancel.sqlite3"))
    with TestClient(create_app()) as client:
        session_id = client.post("/api/studio/sessions", json={}).json()["id"]
        first = client.post(f"/api/studio/sessions/{session_id}/messages",
                            json={"message": "用画笔画一张笑脸"}).json()["id"]
        second = client.post(f"/api/studio/sessions/{session_id}/messages",
                             json={"message": "画一个蓝色圆形"}).json()["id"]
        response = client.post(f"/api/studio/tasks/{second}/cancel")
        assert response.status_code == 200
        for _ in range(150):
            first_task = client.get(f"/api/studio/tasks/{first}").json()
            second_task = client.get(f"/api/studio/tasks/{second}").json()
            if first_task["status"] == "completed" and second_task["status"] == "cancelled":
                break
            time.sleep(0.025)
        assert first_task["status"] == "completed", first_task["error"]
        assert second_task["status"] == "cancelled"
        scene = client.get(f"/api/studio/sessions/{session_id}").json()["document"]
        assert len(scene["elements"]) == 4

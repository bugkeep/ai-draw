import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from studio.agent_tools import validate_tool
from studio.agents import DrawingAgents, prepare_drawing_tool
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore, VersionConflict


def workspace(tmp_path):
    store = StudioStore(tmp_path / "agents.sqlite3")
    runtime = DrawingRuntime(store, ModelSettings(store))
    session = store.create_session("连续作画")
    task = store.create_task(session["id"])
    return store, runtime, session, task


def start_run(store, runtime, session, task, agent):
    run = store.agents.next_run(session["id"], agent["agent_id"], task["id"])
    return runtime.children._initialize(session["id"], run)


def native_tool(store, run, call_id, name, args):
    encoded = json.dumps(args, ensure_ascii=False)
    assert store.agents.begin_call(run["owner_task_id"], run["id"], call_id, name, encoded) is None
    draft, selection, actions, output = prepare_drawing_tool(
        name, args, run["document"], run["draft"], run["selection"],
    )
    run["draft"], run["selection"] = draft, selection
    run["prepared"].extend(actions)
    store.agents.finish_call(run["id"], call_id, output, run)
    return output


def test_input_queue_has_stable_ids_and_reuses_final_agent(tmp_path):
    store, runtime, session, task = workspace(tmp_path)
    agent = store.agents.create_agent(session["id"], task["id"], "spawn-house", "绘制房屋", "Hypatia", [])
    repeated = store.agents.create_agent(session["id"], task["id"], "spawn-house", "绘制房屋", "Hypatia", [])
    assert (agent["agent_id"], agent["input_id"], agent["run_id"]) == (
        repeated["agent_id"], repeated["input_id"], repeated["run_id"])
    second = store.agents.enqueue_input(session["id"], task["id"], agent["agent_id"], "add-window", "增加窗户", [])
    third = store.agents.enqueue_input(session["id"], task["id"], agent["agent_id"], "add-door", "增加门", [])
    assert len({agent["input_id"], second["input_id"], third["input_id"]}) == 3
    assert len({agent["run_id"], second["run_id"], third["run_id"]}) == 3
    for expected in (agent, second, third):
        run = start_run(store, runtime, session, task, agent)
        assert run["id"] == expected["run_id"]
        store.agents.complete_run(session["id"], run["id"], "输入已处理")
    assert store.agents.get_agent(session["id"], agent["agent_id"])["state"] == "Final"
    followup = store.agents.enqueue_input(session["id"], task["id"], agent["agent_id"], "recolor", "修改屋顶颜色", [])
    assert followup["agent_id"] == agent["agent_id"] and followup["state"] == "Running"
    assert followup["input_id"] != third["input_id"]
    assert store.get_document(session["document"]["id"])["elements"] == []
    store.close()


def test_wait_only_observes_and_returns_on_first_ready_child(tmp_path):
    async def check():
        store, runtime, session, task = workspace(tmp_path)
        one = store.agents.create_agent(session["id"], task["id"], "one", "绘制房屋", "House", [])
        two = store.agents.create_agent(session["id"], task["id"], "two", "绘制树木", "Tree", [])
        waiter = asyncio.create_task(runtime.children.wait_agent(session["id"], [one["agent_id"], two["agent_id"]]))
        await asyncio.sleep(0)
        assert not waiter.done() and runtime.children.workers == {}
        run = start_run(store, runtime, session, task, one)
        native_tool(store, run, "rectangle", "create_element", {"type": "rectangle", "name": "房屋"})
        result = store.agents.complete_run(session["id"], run["id"], "房屋已经准备")
        await runtime.children._notify(session["id"], one["agent_id"], result)
        observed = await waiter
        assert len(observed["results"]) == 1 and observed["results"][0]["action_count"] == 1
        assert {agent["state"] for agent in observed["agents"]} == {"Final", "Running"}
        again = await runtime.children.wait_agent(session["id"], [one["agent_id"]],
                                                 {one["agent_id"]: observed["revisions"][one["agent_id"]]})
        assert again["results"] == [] and again["agents"][0]["state"] == "Final"
        store.agents.pause_agent(session["id"], two["agent_id"])
        closed = await runtime.children.wait_agent(session["id"], [two["agent_id"]])
        assert closed["agents"][0]["state"] == "Shutdown"
        assert runtime.children.workers == {}
        assert store.get_document(session["document"]["id"])["elements"] == []
        store.close()
    asyncio.run(check())


def test_shutdown_and_restart_preserve_native_progress_and_original_input(tmp_path):
    store, runtime, session, task = workspace(tmp_path)
    agent = store.agents.create_agent(session["id"], task["id"], "shape", "绘制矩形并移动", "Hypatia", [])
    run = start_run(store, runtime, session, task, agent)
    output = native_tool(store, run, "create", "create_element", {"type": "rectangle", "x": 20, "name": "房屋"})
    store.agents.pause_agent(session["id"], agent["agent_id"])
    with pytest.raises(ValueError, match="Resume"):
        store.agents.enqueue_input(session["id"], task["id"], agent["agent_id"], "closed-input", "增加窗户", [])
    assert len(store.agents.get_run(session["id"], run["id"])["prepared"]) == 1
    path = store.database_path
    store.close()
    reopened = StudioStore(path)
    resumed_runtime = DrawingRuntime(reopened, ModelSettings(reopened))
    next_task = reopened.create_task(session["id"])
    resumed = reopened.agents.resume_agent(session["id"], next_task["id"], agent["agent_id"])
    assert resumed["agent_id"] == agent["agent_id"] and resumed["nickname"] == "Hypatia"
    continued = start_run(reopened, resumed_runtime, session, next_task, agent)
    assert continued["id"] == run["id"] and continued["input_id"] == agent["input_id"]
    assert continued["draft"] == run["draft"] and continued["prepared"] == run["prepared"]
    assert reopened.agents.begin_call(next_task["id"], run["id"], "create", "create_element",
                                      json.dumps({"type": "rectangle", "x": 20, "name": "房屋"}, ensure_ascii=False)) == output
    native_tool(reopened, continued, "move", "move_elements", {"ids": output["changed_ids"], "dx": 10, "dy": 0})
    result = reopened.agents.complete_run(session["id"], run["id"], "矩形与移动动作已经准备")
    asyncio.run(resumed_runtime.apply_result(next_task["id"], session["id"], session["document"]["id"], result["result_id"]))
    document = reopened.get_document(session["document"]["id"])
    assert len(document["elements"]) == 1 and document["elements"][0]["x"] == 30
    reopened.agents.pause_agent(session["id"], agent["agent_id"])
    assert reopened.agents.resume_agent(session["id"], next_task["id"], agent["agent_id"])["state"] == "Final"
    assert reopened.agents.next_run(session["id"], agent["agent_id"], next_task["id"]) is None
    reopened.close()


def test_submission_checkpoint_survives_cancellation_and_does_not_repeat_moves(tmp_path):
    async def check():
        store, runtime, session, task = workspace(tmp_path)
        agent = store.agents.create_agent(session["id"], task["id"], "shape", "绘制并调整房屋", "House", [])
        run = start_run(store, runtime, session, task, agent)
        created = native_tool(store, run, "create", "create_element", {"type": "rectangle", "x": 20})
        native_tool(store, run, "move", "move_elements", {"ids": created["changed_ids"], "dx": 10, "dy": 0})
        native_tool(store, run, "move-again", "move_elements", {"ids": created["changed_ids"], "dx": 15, "dy": 0})
        result = store.agents.complete_run(session["id"], run["id"], "三个动作已经准备")
        submitting = asyncio.create_task(runtime.apply_result(task["id"], session["id"], session["document"]["id"], result["result_id"]))
        await asyncio.sleep(0)
        submitting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await submitting
        assert store.agents.get_result(session["id"], result["result_id"])["committed_count"] == 1
        await runtime.apply_result(task["id"], session["id"], session["document"]["id"], result["result_id"])
        document = store.get_document(session["document"]["id"])
        assert len(document["elements"]) == 1 and document["elements"][0]["x"] == 45
        assert document["version"] == 3
        await runtime.apply_result(task["id"], session["id"], session["document"]["id"], result["result_id"])
        assert store.get_document(session["document"]["id"]) == document
        assert store.history_step(document["id"], "undo")["elements"][0]["x"] == 30
        assert store.history_step(document["id"], "redo")["elements"][0]["x"] == 45
        store.close()
    asyncio.run(check())


def test_queued_followup_can_edit_prior_private_shapes_with_ordered_dependencies(tmp_path):
    store, runtime, session, task = workspace(tmp_path)
    agent = store.agents.create_agent(session["id"], task["id"], "create", "绘制房屋", "House", [])
    store.agents.enqueue_input(session["id"], task["id"], agent["agent_id"], "move", "把房屋向右移动", [])
    first = start_run(store, runtime, session, task, agent)
    created = native_tool(store, first, "house", "create_element", {"type": "rectangle", "x": 20, "name": "房屋"})
    first_result = store.agents.complete_run(session["id"], first["id"], "房屋已经准备")
    second = start_run(store, runtime, session, task, agent)
    assert second["draft"][0]["id"] == created["changed_ids"][0]
    native_tool(store, second, "move-house", "move_elements", {"ids": created["changed_ids"], "dx": 40, "dy": 0})
    second_result = store.agents.complete_run(session["id"], second["id"], "房屋位置已经修改")
    with pytest.raises(ValueError, match="dependency"):
        asyncio.run(runtime.apply_result(task["id"], session["id"], session["document"]["id"], second_result["result_id"]))
    asyncio.run(runtime.apply_result(task["id"], session["id"], session["document"]["id"], first_result["result_id"]))
    asyncio.run(runtime.apply_result(task["id"], session["id"], session["document"]["id"], second_result["result_id"]))
    assert store.get_document(session["document"]["id"])["elements"][0]["x"] == 60
    store.close()


def test_element_conflict_preserves_result_cursor_and_other_session(tmp_path):
    store, runtime, session, task = workspace(tmp_path)
    document_id = session["document"]["id"]
    created = store.commit_action(document_id, {"kind": "create_element", "args": {"type": "rectangle", "x": 20}}, {})
    agent = store.agents.create_agent(session["id"], task["id"], "move", "移动矩形", "Move", [])
    run = start_run(store, runtime, session, task, agent)
    native_tool(store, run, "move-one", "move_elements", {"ids": created["changed_ids"], "dx": 40, "dy": 0})
    result = store.agents.complete_run(session["id"], run["id"], "移动动作已经准备")
    store.commit_action(document_id, {"kind": "move_elements", "args": {"ids": created["changed_ids"], "dx": 5, "dy": 0}}, {})
    before = store.get_document(document_id)
    with pytest.raises(VersionConflict):
        asyncio.run(runtime.apply_result(task["id"], session["id"], document_id, result["result_id"]))
    assert store.agents.get_result(session["id"], result["result_id"])["committed_count"] == 0
    assert store.get_document(document_id) == before
    other = store.create_session("独立画布")
    other_task = store.create_task(other["id"])
    for operation in (
        lambda: store.agents.get_agent(other["id"], agent["agent_id"]),
        lambda: store.agents.enqueue_input(other["id"], other_task["id"], agent["agent_id"], "foreign", "移动", []),
        lambda: store.agents.get_run(other["id"], run["id"]),
        lambda: asyncio.run(runtime.apply_result(other_task["id"], other["id"], other["document"]["id"], result["result_id"])),
    ):
        with pytest.raises(KeyError):
            operation()
    preserved = store.get_session(other["id"])
    assert preserved["document"] == other["document"] and preserved["messages"] == []
    assert [item["id"] for item in preserved["tasks"]] == [other_task["id"]]
    store.close()


def test_tool_call_results_are_correlated_private_and_idempotent(tmp_path):
    store, runtime, session, task = workspace(tmp_path)
    agent = store.agents.create_agent(session["id"], task["id"], "one", "绘制矩形", "House", [])
    run = start_run(store, runtime, session, task, agent)
    output = native_tool(store, run, "native-create", "create_element", {"type": "rectangle"})
    recorded = store.connection.execute("SELECT payload FROM agent_messages WHERE run_id=?", (run["id"],)).fetchone()[0]
    event = json.loads(recorded)
    assert event["type"] == "function_call_output" and event["call_id"] == "native-create"
    assert json.loads(event["output"]) == output
    with pytest.raises(ValueError, match="identical"):
        store.agents.begin_call(task["id"], run["id"], "native-create", "create_element", '{"type":"ellipse"}')
    store.agents.complete_run(session["id"], run["id"], "房屋已经准备")
    observation = runtime.children.observe(session["id"], [agent["agent_id"]], {})
    assert "points" not in json.dumps(observation) and "tool_calls" not in json.dumps(observation)
    assert "prepared" not in json.dumps(observation)
    assert "agent_messages" not in store.get_session(session["id"])
    assert validate_tool("spawn_agent", {"message": "绘制场景" * 1000})["message"] == "绘制场景" * 1000
    for name in ("spawn_agent", "send_input", "wait_agent", "close_agent", "resume_agent", "apply_agent_result"):
        with pytest.raises(ValueError, match="cannot create or delegate"):
            validate_tool(name, {}, child=True)
    store.close()


def test_main_context_retains_submitted_result_ids_when_tool_history_is_archived(tmp_path):
    store, runtime, session, task = workspace(tmp_path)
    agent = store.agents.create_agent(session["id"], task["id"], "create-house", "绘制左侧房屋", "House", [])
    run = start_run(store, runtime, session, task, agent)
    native_tool(store, run, "house", "create_element", {"type": "rectangle", "name": "房屋"})
    result = store.agents.complete_run(session["id"], run["id"], "左侧房屋已经准备")
    asyncio.run(runtime.apply_result(task["id"], session["id"], session["document"]["id"], result["result_id"]))
    models = DrawingAgents(store, ModelConfig(provider="ollama", model="llama3.1", api_base="http://localhost:11434"))
    messages = models.main_messages(task["id"], "绘制左侧房屋", store.get_document(session["document"]["id"]), [], [])
    prompt = json.loads(messages[1]["content"])
    submitted = prompt["task_progress"]["submitted_results"]
    assert len(submitted) == 1 and submitted[0]["result_id"] == result["result_id"]
    assert submitted[0]["committed_count"] == submitted[0]["action_count"] == 1
    assert prompt["task_progress"]["inputs"][0]["input_id"] == agent["input_id"]
    assert prompt["child_agents"][0]["latest_result"]["result_id"] == result["result_id"]
    assert prompt["pending_results"] == []
    assert '"prepared":' not in messages[1]["content"] and '"tool_calls":' not in messages[1]["content"]
    assert store.agents.get_run(session["id"], run["id"])["prepared"]
    store.close()


def test_delete_session_cascades_agent_context_and_preserves_other_canvas(tmp_path, monkeypatch):
    path = tmp_path / "http-agents.sqlite3"
    monkeypatch.setenv("DRAW_AGENT_DB", str(path))
    app = create_app()
    with TestClient(app) as client:
        session = client.post("/api/studio/sessions", json={"title": "要删除的会话"}).json()
        other = client.post("/api/studio/sessions", json={"title": "保留的会话"}).json()
        store, runtime = app.state.studio_store, app.state.drawing_runtime
        task = store.create_task(session["id"])
        agent = store.agents.create_agent(session["id"], task["id"], "one", "绘制房屋", "House", [])
        run = start_run(store, runtime, session, task, agent)
        native_tool(store, run, "create", "create_element", {"type": "rectangle"})
        store.agents.complete_run(session["id"], run["id"], "房屋已经准备")
        assert client.get(f"/api/studio/sessions/{session['id']}/agents").json()[0]["agent_id"] == agent["agent_id"]
        assert client.get(f"/api/studio/sessions/{other['id']}/agents/{agent['agent_id']}").status_code == 404
        assert client.delete(f"/api/studio/sessions/{session['id']}").status_code == 200
        for table in ("agent_sessions", "agent_inputs", "agent_runs", "agent_messages", "agent_tool_calls", "drawing_results"):
            assert store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert client.get(f"/api/studio/sessions/{other['id']}").json() == other


def test_completion_validates_child_work_and_preserves_tool_errors(tmp_path):
    async def check():
        store, runtime, session, task = workspace(tmp_path)
        models = DrawingAgents(store, ModelConfig(provider="local", model="drawing-model", api_base="http://localhost:1234/v1"))

        async def call(call_id, name, arguments, **kwargs):
            return await runtime.call_main_tool(models, task["id"], session["id"], session["document"]["id"], {
                "id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)},
            }, **kwargs)

        invalid = await call("invalid", "move_elements", {"ids": ["missing"], "dx": 10, "dy": 0})
        assert invalid["ok"] is False and "not found" in invalid["error"].lower()
        refused = await call("finish-with-error", "finish_drawing", {"summary": "完成"},
                             completion_error="Read and handle every tool error before completing.")
        assert refused["ok"] is False and "error" in refused["error"]
        child = store.agents.create_agent(session["id"], task["id"], "spawn", "绘制房屋", "House", [])
        assert not (await call("finish-running", "finish_drawing", {"summary": "完成"}))["ok"]
        run = start_run(store, runtime, session, task, child)
        native_tool(store, run, "create", "create_element", {"type": "rectangle"})
        result = store.agents.complete_run(session["id"], run["id"], "房屋已准备")
        assert not (await call("finish-pending", "finish_drawing", {"summary": "完成"}))["ok"]
        await runtime.apply_result(task["id"], session["id"], session["document"]["id"], result["result_id"])
        assert (await call("finish", "finish_drawing", {"summary": "完成"}))["ok"]
        errors = [json.loads(item["payload"]["output"]) for item in store.tool_logs(task["id"])
                  if item["kind"] == "function_call_output" and item["payload"]["call_id"] == "invalid"]
        assert errors == [invalid]
        store.close()
    asyncio.run(check())

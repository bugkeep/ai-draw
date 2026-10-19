import argparse
import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv

from studio.agents import DrawingAgents
from studio.llm import close_model_clients
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore


async def evaluate(database: Path) -> dict:
    if database.resolve() == Path("runs/studio.sqlite3").resolve():
        raise ValueError("Use a separate evaluation database")
    store = StudioStore(database)
    settings = ModelSettings(store)
    runtime = DrawingRuntime(store, settings)
    session = store.create_session("子 Agent 生命周期评测")
    other = store.create_session("独立画布")
    task = store.create_task(session["id"])
    store.update_task(task["id"], "running")
    models = DrawingAgents(store, settings.load())
    document_id = session["document"]["id"]
    started = time.perf_counter()
    trace = []
    checks = {}

    async def call(name: str, arguments: dict, call_id: str) -> dict:
        output = await runtime.call_main_tool(models, task["id"], session["id"], document_id, {
            "id": call_id, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        })
        trace.append({"name": name, "call_id": call_id, "output": output})
        if output.get("ok") is False:
            raise RuntimeError(output["error"])
        print(json.dumps({"tool": name, "agent_id": output.get("agent_id"), "state": output.get("state")}), flush=True)
        return output

    async def wait_all(ids: list[str]):
        after = {}
        while True:
            observed = await call("wait_agent", {"ids": ids, "after": after}, f"wait-{len(trace)}")
            if all(agent["state"] == "Final" for agent in observed["agents"]):
                return observed
            after = observed["revisions"]
            ids = [agent["agent_id"] for agent in observed["agents"] if agent["state"] == "Running"]
            after = {agent_id: after[agent_id] for agent_id in ids}

    try:
        house_args = {"message": "只创建一个可编辑矩形，名称为房屋，x=100、y=100、width=100、height=60，填充蓝色 #3b82f6。无需其他图形。", "nickname": "Hypatia"}
        house = await call("spawn_agent", house_args, "spawn-house")
        queued = await call("send_input", {"agent_id": house["agent_id"],
                            "message": "把本子 Agent 上一次准备的房屋矩形向右移动40个画布单位，保留相同对象ID、颜色与尺寸。只准备移动动作。"}, "queue-move")
        sky = await call("spawn_agent", {"message": "只创建一个椭圆，名称为太阳，x=600、y=80、width=70、height=70，填充黄色 #facc15。无需其他图形。", "nickname": "Gauss"}, "spawn-sky")
        repeated = await call("spawn_agent", house_args, "spawn-house")
        checks["spawn_idempotent"] = repeated == house and len(store.agents.list_agents(session["id"])) == 2
        checks["unique_input_and_run_ids"] = house["input_id"] != queued["input_id"] and house["run_id"] != queued["run_id"]
        while True:
            first = store.agents.get_run(session["id"], house["run_id"])
            if first["prepared"]:
                break
            if first["status"] == "failed":
                raise RuntimeError(first["error"])
            await asyncio.sleep(0.03)
        closed = await call("close_agent", {"agent_id": house["agent_id"]}, "close-house")
        snapshot = store.agents.get_run(session["id"], house["run_id"])
        checks["shutdown_retains_prepared_progress"] = closed["state"] == "Shutdown" and len(snapshot["prepared"]) == 1
        observed = await call("wait_agent", {"ids": [house["agent_id"]]}, "observe-closed")
        checks["wait_does_not_resume"] = observed["agents"][0]["state"] == "Shutdown" and house["agent_id"] not in runtime.children.workers
        resumed = await call("resume_agent", {"agent_id": house["agent_id"]}, "resume-house")
        checks["resume_preserves_identity"] = resumed["agent_id"] == house["agent_id"] and resumed["nickname"] == "Hypatia"
        await wait_all([house["agent_id"], sky["agent_id"]])
        runs = store.agents.task_runs(session["id"], task["id"])
        checks["queued_inputs_complete_in_order"] = all(run["status"] == "completed" for run in runs) and len(runs) == 3
        checks["children_do_not_commit"] = store.get_document(document_id)["elements"] == []
        results = store.agents.pending_results(session["id"])
        for result in results:
            await call("apply_agent_result", {"result_id": result["id"]}, f"apply-{result['id']}")
        document = store.get_document(document_id)
        rectangle = next(item for item in document["elements"] if item["type"] == "rectangle")
        checks["queued_followup_moves_original_object"] = len(document["elements"]) == 2 and rectangle["x"] == 140 and rectangle["y"] == 100
        first_result = next(result for result in results if result["run_id"] == house["run_id"])
        await call("apply_agent_result", {"result_id": first_result["id"]}, "apply-again")
        checks["repeated_submission_is_idempotent"] = store.get_document(document_id) == document
        final_input = await call("send_input", {"agent_id": house["agent_id"],
                                 "message": f"把对象 {rectangle['id']} 向下移动20个画布单位，其余对象保持不变。",
                                 "relevant_ids": [rectangle["id"]]}, "final-to-running")
        checks["final_accepts_new_input"] = final_input["state"] == "Running" and final_input["agent_id"] == house["agent_id"]
        await wait_all([house["agent_id"]])
        for result in store.agents.pending_results(session["id"]):
            await call("apply_agent_result", {"result_id": result["id"]}, f"apply-final-{result['id']}")
        final = store.get_document(document_id)
        updated = next(item for item in final["elements"] if item["id"] == rectangle["id"])
        checks["continuous_edit_preserves_object"] = updated["x"] == 140 and updated["y"] == 120 and len(final["elements"]) == 2
        await call("close_agent", {"agent_id": house["agent_id"]}, "close-final")
        idle = await call("resume_agent", {"agent_id": house["agent_id"]}, "resume-final")
        checks["resume_without_pending_work_is_final"] = idle["state"] == "Final" and idle["inputs"] == []
        checks["canvas_isolation"] = store.get_session(other["id"]) == other and store.agents.list_agents(other["id"]) == []
        checks["raw_records_are_private"] = "tool_logs" not in store.get_session(session["id"]) and all(
            '"prepared":' not in json.dumps(item["output"]) for item in trace if item["name"] in {"spawn_agent", "send_input", "wait_agent"})
        logs = store.tool_logs(task["id"])
        intervals = [(item["agent_id"], item["created_at"] - item["payload"]["latency_ms"] / 1000, item["created_at"])
                     for item in logs if item["kind"] == "llm_response" and item["agent_id"].startswith("agent_")]
        checks["parallel_model_requests"] = any(
            first[0] != second[0] and max(first[1], second[1]) < min(first[2], second[2])
            for first in intervals for second in intervals)
        store.update_task(task["id"], "completed" if all(checks.values()) else "failed")
        return {"provider": models.settings.provider, "model": models.settings.model,
                "session_id": session["id"], "task_id": task["id"], "database": str(database),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "checks": checks, "passed": all(checks.values()), "trace": trace,
                "child_model_responses": len(intervals), "element_count": len(final["elements"])}
    finally:
        await runtime.close()
        await close_model_clients()
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("runs/agent-lifecycle-evaluation.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("runs/agent-lifecycle-evaluation.json"))
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    result = asyncio.run(evaluate(args.database))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "trace"}, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

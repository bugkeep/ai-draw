import argparse
import json
import time
from pathlib import Path

import httpx

from studio.actions import element_fingerprint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("runs/agent-reuse-evaluation.json"))
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=15) as client:
        def request(method, path, data=None):
            response = client.request(method, f"/api/studio{path}", json=data)
            response.raise_for_status()
            return response.json()

        def send(message):
            task = request("POST", f"/sessions/{args.session_id}/messages", {"message": message})
            try:
                while True:
                    task = request("GET", f"/tasks/{task['id']}")
                    if task["status"] in {"completed", "partial", "failed", "cancelled"}:
                        return task
                    time.sleep(0.2)
            except KeyboardInterrupt:
                request("POST", f"/tasks/{task['id']}/cancel")
                raise

        agents = request("GET", f"/sessions/{args.session_id}/agents")
        house = next(agent for agent in agents if "房" in agent["nickname"] or "house" in agent["nickname"].lower())
        before = request("GET", f"/sessions/{args.session_id}")["document"]
        target = max((item for item in before["elements"] if item["type"] == "rectangle"
                      and not item.get("isDeleted") and item["x"] < 500), key=lambda item: item["width"] * item["height"])
        fingerprints = {item["id"]: element_fingerprint(item) for item in before["elements"] if item["id"] != target["id"]}
        task = send(f"请继续使用子 Agent {house['agent_id']}（{house['nickname']}）的独立会话，调用 send_input 让它只把对象 {target['id']} 向右移动17个画布单位，其他所有对象保持不变；由你审核并提交结果。")
        after = request("GET", f"/sessions/{args.session_id}")["document"]
        current = request("GET", f"/sessions/{args.session_id}/agents")
        updated = next(item for item in after["elements"] if item["id"] == target["id"])
        logs = request("GET", f"/tasks/{task['id']}/logs")
        calls = [item["payload"]["name"] for item in logs if item["agent_id"] == "main" and item["kind"] == "function_call"]
        reused = next(agent for agent in current if agent["agent_id"] == house["agent_id"])
        checks = {
            "completed": task["status"] == "completed",
            "real_main_model": any(item["agent_id"] == "main" and item["kind"] == "llm_response" for item in logs),
            "same_agent_set": {agent["agent_id"] for agent in current} == {agent["agent_id"] for agent in agents},
            "send_input_and_apply": "send_input" in calls and "apply_agent_result" in calls and "spawn_agent" not in calls,
            "new_input_same_agent": reused["latest_result"]["input_id"] != house["latest_result"]["input_id"],
            "same_object_moved": len(after["elements"]) == len(before["elements"]) and updated["x"] == target["x"] + 17 and updated["y"] == target["y"],
            "other_objects_preserved": fingerprints == {item["id"]: element_fingerprint(item) for item in after["elements"] if item["id"] != target["id"]},
        }
        commits = sum(item["kind"] == "commit" for item in logs)
        for _ in range(commits):
            undone = request("POST", f"/documents/{before['id']}/history", {"direction": "undo"})
        checks["undo"] = commits > 0 and [element_fingerprint(item) for item in undone["elements"]] == [element_fingerprint(item) for item in before["elements"]]
        for _ in range(commits):
            redone = request("POST", f"/documents/{before['id']}/history", {"direction": "redo"})
        checks["redo"] = commits > 0 and [element_fingerprint(item) for item in redone["elements"]] == [element_fingerprint(item) for item in after["elements"]]
        if all(checks.values()):
            lifecycle = send(f"请调用 close_agent 关闭子 Agent {house['agent_id']}，随后调用 resume_agent 恢复同一个 Agent。它的输入已经全部完成，此次只检查关闭与恢复状态，保持画布内容不变。")
            lifecycle_logs = request("GET", f"/tasks/{lifecycle['id']}/logs")
            names = {item["payload"]["call_id"]: item["payload"]["name"] for item in lifecycle_logs
                     if item["agent_id"] == "main" and item["kind"] == "function_call"}
            states = {}
            for item in lifecycle_logs:
                if item["agent_id"] == "main" and item["kind"] == "function_call_output":
                    states[names[item["payload"]["call_id"]]] = json.loads(item["payload"]["output"]).get("state")
            final_agent = request("GET", f"/sessions/{args.session_id}/agents/{house['agent_id']}")
            checks["main_closes_and_resumes"] = lifecycle["status"] == "completed" and states.get("close_agent") == "Shutdown" and states.get("resume_agent") == "Final"
            checks["completed_input_not_restarted"] = final_agent["latest_result"]["run_id"] == reused["latest_result"]["run_id"] and final_agent["state"] == "Final"
            final_document = request("GET", f"/sessions/{args.session_id}")["document"]
            checks["lifecycle_preserves_canvas"] = [element_fingerprint(item) for item in final_document["elements"]] == [element_fingerprint(item) for item in after["elements"]]
        result = {"session_id": args.session_id, "agent_id": house["agent_id"], "task_id": task["id"],
                  "status": task["status"], "error": task["error"], "latency_ms": task["latency_ms"],
                  "main_tool_calls": calls, "checks": checks, "passed": all(checks.values())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

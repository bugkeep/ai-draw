import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("runs/studio-multiagent-evaluation.json"))
    parser.add_argument("--session-id")
    args = parser.parse_args()
    prompt = "在同一张画布上画一个完整的公园场景：左侧画一栋房子，右侧画两棵树，天空画太阳和云朵。请安排多个子 Agent 分别准备独立部分。"
    with httpx.Client(base_url=args.url, timeout=15) as client:
        if args.session_id:
            session_id = args.session_id
            session_response = client.get(f"/api/studio/sessions/{session_id}")
            session_response.raise_for_status()
            task_id = session_response.json()["tasks"][0]["id"]
            task_response = client.get(f"/api/studio/tasks/{task_id}")
            task_response.raise_for_status()
            task = task_response.json()
        else:
            health = client.get("/api/studio/health")
            health.raise_for_status()
            if not health.json()["provider_configured"]:
                raise RuntimeError("ARK_API_KEY is required for this evaluation")
            session_response = client.post("/api/studio/sessions", json={"title": "并行 Agent 评测"})
            session_response.raise_for_status()
            session_id = session_response.json()["id"]
            task_response = client.post(f"/api/studio/sessions/{session_id}/messages", json={"message": prompt})
            task_response.raise_for_status()
            task_id = task_response.json()["id"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                task_response = client.get(f"/api/studio/tasks/{task_id}")
                task_response.raise_for_status()
                task = task_response.json()
                if task["status"] in {"completed", "partial", "failed", "cancelled"}:
                    break
                time.sleep(0.3)
            else:
                raise TimeoutError("Multi-agent task exceeded 180 seconds")
        scene_response = client.get(f"/api/studio/sessions/{session_id}")
        scene_response.raise_for_status()
        logs_response = client.get(f"/api/studio/tasks/{task_id}/logs")
        logs_response.raise_for_status()
        logs = logs_response.json()
        scene = scene_response.json()["document"]
    elements = [item for item in scene["elements"] if not item.get("isDeleted")]
    def named(item, *words):
        name = str(item.get("customData", {}).get("name", "")).lower()
        return any(word in name for word in words)

    sun = next((item for item in elements if item["type"] == "ellipse"
                and named(item, "太阳", "sun") and item["y"] < 220), None)
    trunks = [item for item in elements if item["type"] == "rectangle"
              and named(item, "树干", "trunk") and item["x"] >= 500]
    rays = [item for item in elements if named(item, "光芒", "ray")]
    scene_checks = {
        "house_left": any(named(item, "房", "屋", "house") and item["x"] < 500 for item in elements),
        "two_trees_right": len({round(item["x"] / 40) for item in trunks}) >= 2,
        "sun_above": sun is not None,
        "cloud_above": any(named(item, "云", "cloud") and item["y"] < 250 for item in elements),
        "rays_near_sun": sun is not None and all(
            sun["x"] - 100 <= item["x"] <= sun["x"] + sun["width"] + 100
            and sun["y"] - 100 <= item["y"] <= sun["y"] + sun["height"] + 100
            for item in rays),
    }
    child_ids = sorted({item["agent_id"] for item in logs if item["agent_id"].startswith("child:")})
    reports = [item["payload"] for item in logs if item["kind"] == "child_report"]
    commits = [item for item in logs if item["kind"] == "commit"]
    result = {"session_id": session_id, "task_id": task_id, "status": task["status"],
              "error": task["error"], "latency_ms": task["latency_ms"], "child_agents": child_ids,
              "child_count": len(child_ids), "child_reports": [
                  {"id": item["id"], "success": item["success"], "error": item.get("error", "")}
                  for item in reports],
              "committed_actions": len(commits),
              "element_count": len(elements), "scene_checks": scene_checks,
              "passed": task["status"] == "completed" and len(reports) >= 3
              and all(item["success"] for item in reports) and len(commits) >= 8
              and all(scene_checks.values())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

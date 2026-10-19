import argparse
import json
import time
from pathlib import Path

import httpx

from .evaluate_agent_edit import is_green


def tree_trunks_with_canopies(elements: list[dict]) -> list[dict]:
    canopies = [item for item in elements if item["type"] in {"ellipse", "freedraw"}
                and is_green(item.get("backgroundColor", ""))]
    trunks = []
    for item in elements:
        color = item.get("backgroundColor", "")
        if item["type"] != "rectangle" or item["x"] < 500 or item["height"] < item["width"] * 2 or len(color) != 7:
            continue
        red, green, blue = bytes.fromhex(color[1:])
        center = item["x"] + item["width"] / 2
        if red > green > blue and any(
            leaf["x"] <= center <= leaf["x"] + leaf["width"] and leaf["y"] < item["y"]
            and leaf["y"] + leaf["height"] >= item["y"] - item["width"] * 2 for leaf in canopies
        ):
            trunks.append(item)
    return trunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("runs/studio-multiagent-evaluation.json"))
    parser.add_argument("--session-id")
    args = parser.parse_args()
    prompt = "在同一张画布上画一个完整的公园场景：左侧画一栋房子，右侧画两棵树，天空画太阳和云朵。请安排三个子 Agent 分别准备房子、两棵树和天空部分。"
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
                raise RuntimeError("Configure a model in the settings page before running this evaluation")
            session_response = client.post("/api/studio/sessions", json={"title": "并行 Agent 评测"})
            session_response.raise_for_status()
            session_id = session_response.json()["id"]
            task_response = client.post(f"/api/studio/sessions/{session_id}/messages", json={"message": prompt})
            task_response.raise_for_status()
            task_id = task_response.json()["id"]
            try:
                while True:
                    task_response = client.get(f"/api/studio/tasks/{task_id}")
                    task_response.raise_for_status()
                    task = task_response.json()
                    if task["status"] in {"completed", "partial", "failed", "cancelled"}:
                        break
                    time.sleep(0.3)
            except KeyboardInterrupt:
                client.post(f"/api/studio/tasks/{task_id}/cancel").raise_for_status()
                raise
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
    trunks = tree_trunks_with_canopies(elements)
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

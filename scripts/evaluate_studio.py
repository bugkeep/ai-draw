import argparse
import json
import math
import statistics
import time
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app


def wait_for_task(client: TestClient, task_id: str, timeout: float = 180) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/studio/tasks/{task_id}").json()
        if task["status"] in {"completed", "partial", "failed", "cancelled"}:
            return task
        time.sleep(0.2)
    raise TimeoutError(f"Task {task_id} did not finish within {timeout} seconds")


def send(client: TestClient, session_id: str, prompt: str) -> dict:
    response = client.post(f"/api/studio/sessions/{session_id}/messages", json={"message": prompt})
    response.raise_for_status()
    return wait_for_task(client, response.json()["id"])


def visible(session: dict) -> list[dict]:
    return [element for element in session["document"]["elements"] if not element.get("isDeleted")]


def evaluate(client: TestClient) -> dict:
    session = client.post("/api/studio/sessions", json={"title": "真实操作评测"}).json()
    session_id = session["id"]
    observations = []

    def step(prompt, judge):
        task = send(client, session_id, prompt)
        current = client.get(f"/api/studio/sessions/{session_id}").json()
        passed = task["status"] == "completed" and judge(current)
        observations.append({"prompt": prompt, "passed": passed, "status": task["status"],
                             "latency_ms": task["latency_ms"], "error": task["error"],
                             "element_count": len(visible(current))})
        return current

    scene = step("画一个蓝色圆形，名字叫主圆。", lambda current: any(item["type"] == "ellipse" for item in visible(current)))
    circles = [item for item in visible(scene) if item["type"] == "ellipse"]
    if circles:
        circle_id = circles[-1]["id"]
        original_x = circles[-1]["x"]
        scene = step("把它向右移动 80 像素。", lambda current: next(
            (item["x"] > original_x + 50 for item in visible(current) if item["id"] == circle_id), False))
        scene = step("选中刚才的圆形。", lambda current: circle_id in current["document"]["selection"])
        step("撤销", lambda current: next(
            (item["x"] == original_x for item in visible(current) if item["id"] == circle_id), False))
        step("重做", lambda current: next(
            (item["x"] > original_x + 50 for item in visible(current) if item["id"] == circle_id), False))
    step("用画笔画一张笑脸，分多笔显示轮廓、眼睛和嘴巴。", lambda current: sum(
        item["type"] == "freedraw" for item in visible(current)) >= 2)
    passed = sum(item["passed"] for item in observations)
    latencies = [item["latency_ms"] for item in observations]
    return {"session_id": session_id, "cases": observations, "passed": passed,
            "total": len(observations), "success_rate": passed / len(observations),
            "median_latency_ms": statistics.median(latencies),
            "p95_latency_ms": sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/studio-evaluation.json"))
    args = parser.parse_args()
    with TestClient(create_app()) as client:
        result = evaluate(client)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("passed", "total", "success_rate", "median_latency_ms", "p95_latency_ms")}, ensure_ascii=False))
    print(args.output.resolve())
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

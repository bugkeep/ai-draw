import argparse
import json
import time
from pathlib import Path

import httpx


def wait_for_task(client: httpx.Client, task_id: str) -> dict:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = client.get(f"/api/studio/tasks/{task_id}")
        response.raise_for_status()
        task = response.json()
        if task["status"] in {"completed", "partial", "failed", "cancelled"}:
            return task
        time.sleep(0.2)
    raise TimeoutError(f"Task {task_id} exceeded 120 seconds")


def is_green(color: str) -> bool:
    if len(color) != 7 or not color.startswith("#"):
        return False
    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    return green > red and green > blue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("runs/studio-agent-edit-evaluation.json"))
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=15) as client:
        health = client.get("/api/studio/health")
        health.raise_for_status()
        if not health.json()["provider_configured"]:
            raise RuntimeError("ARK_API_KEY is required for this evaluation")
        response = client.post("/api/studio/sessions", json={"title": "Agent 多轮指代评测"})
        response.raise_for_status()
        session_id = response.json()["id"]

        def send(message):
            response = client.post(f"/api/studio/sessions/{session_id}/messages", json={"message": message})
            response.raise_for_status()
            return wait_for_task(client, response.json()["id"])

        created_task = send("画一个蓝色圆形，名字叫主圆。")
        response = client.get(f"/api/studio/sessions/{session_id}")
        response.raise_for_status()
        initial = response.json()["document"]["elements"][0]
        edited_task = send("把刚才画的主圆向右移动 70 像素，并把填充改成绿色。")
        response = client.get(f"/api/studio/sessions/{session_id}")
        response.raise_for_status()
        document = response.json()["document"]
        edited = next((item for item in document["elements"] if item["id"] == initial["id"]), None)
        checks = {
            "same_object": edited is not None and len(document["elements"]) == 1,
            "moved_70": edited is not None and abs(edited["x"] - initial["x"] - 70) < 0.01,
            "green_fill": edited is not None and is_green(edited["backgroundColor"]),
        }
    result = {"session_id": session_id, "created_status": created_task["status"],
              "edited_status": edited_task["status"], "latency_ms": edited_task["latency_ms"],
              "checks": checks, "passed": created_task["status"] == "completed"
              and edited_task["status"] == "completed" and all(checks.values())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

import argparse
import json
import time
from pathlib import Path

import httpx


def content(element: dict) -> dict:
    return {key: value for key, value in element.items()
            if key not in {"version", "versionNonce", "updated", "seed", "index"}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("runs/portrait-edit-evaluation.json"))
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=30) as client:
        response = client.get(f"/api/studio/sessions/{args.session_id}")
        response.raise_for_status()
        document = response.json()["document"]
        candidates = [item for item in document["elements"] if not item.get("isDeleted") and item["type"] == "freedraw"
                      and any(word in item.get("customData", {}).get("name", "").lower()
                              for word in ("mouth", "smile", "微笑", "嘴唇", "唇")) and item["width"] > 10]
        if not candidates:
            raise ValueError("The portrait has no named editable mouth stroke")
        target = max(candidates, key=lambda item: item["width"])
        response = client.put(f"/api/studio/documents/{document['id']}/scene", json={
            "version": document["version"], "elements": document["elements"], "selection": [target["id"]],
        })
        response.raise_for_status()
        before = {item["id"]: content(item) for item in response.json()["elements"]}
        prompt = "请只调整选中的微笑曲线，让嘴角稍微上扬，其他笔画保持不变。用已有路径的原对象编号修改曲线。"
        response = client.post(f"/api/studio/sessions/{args.session_id}/messages", json={"message": prompt})
        response.raise_for_status()
        task_id = response.json()["id"]
        print(json.dumps({"task_id": task_id, "selected_id": target["id"]}), flush=True)
        while True:
            response = client.get(f"/api/studio/tasks/{task_id}")
            response.raise_for_status()
            task = response.json()
            if task["status"] in {"completed", "partial", "failed", "cancelled"}:
                break
            time.sleep(0.5)
        response = client.get(f"/api/studio/sessions/{args.session_id}")
        response.raise_for_status()
        after = {item["id"]: content(item) for item in response.json()["document"]["elements"]}
        response = client.get(f"/api/studio/tasks/{task_id}/logs")
        response.raise_for_status()
        logs = response.json()
        checks = {"completed": task["status"] == "completed", "same_object_ids": before.keys() == after.keys(),
                  "selected_curve_changed": before[target["id"]] != after.get(target["id"]),
                  "other_strokes_preserved": all(after.get(key) == value for key, value in before.items() if key != target["id"]),
                  "native_reshape_used": any(item["kind"] == "function_call" and item["payload"]["name"] == "reshape_path" for item in logs)}
        for direction, expected in (("undo", before), ("redo", after)):
            response = client.post(f"/api/studio/documents/{document['id']}/history", json={"direction": direction})
            response.raise_for_status()
            checks[direction] = {item["id"]: content(item) for item in response.json()["elements"]} == expected
    result = {"session_id": args.session_id, "task_id": task_id, "selected_id": target["id"],
              "latency_ms": task["latency_ms"], "error": task["error"], "checks": checks, "passed": all(checks.values())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

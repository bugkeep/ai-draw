import argparse
import json
import time
from pathlib import Path

import httpx

from studio.actions import element_fingerprint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("runs/transformer-evaluation.json"))
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=15) as client:
        def request(method, path, data=None):
            response = client.request(method, f"/api/studio{path}", json=data)
            response.raise_for_status()
            return response.json()

        health = request("GET", "/health")
        if not health["provider_configured"]:
            raise RuntimeError("Configure a real model before running this evaluation")
        session = request("POST", "/sessions", {"title": "Transformer 连续编辑评测"})
        session_id = session["id"]

        def send(message):
            task = request("POST", f"/sessions/{session_id}/messages", {"message": message})
            try:
                while True:
                    task = request("GET", f"/tasks/{task['id']}")
                    if task["status"] in {"completed", "partial", "failed", "cancelled"}:
                        return task
                    time.sleep(0.25)
            except KeyboardInterrupt:
                request("POST", f"/tasks/{task['id']}/cancel")
                raise

        created = send("画一个transformer流程图")
        scene = request("GET", f"/sessions/{session_id}")["document"]
        logs = request("GET", f"/tasks/{created['id']}/logs")
        text = " ".join(item.get("text", "") for item in scene["elements"]).lower()
        checks = {
            "encoder": "encoder" in text or "编码器" in text,
            "decoder": "decoder" in text or "解码器" in text,
            "attention": "attention" in text or "注意力" in text,
            "feed_forward": "feed" in text or "前馈" in text or "ffn" in text,
            "output": "softmax" in text or "输出" in text,
            "connectors": sum(item["type"] in {"line", "arrow"} for item in scene["elements"]) >= 2,
        }
        result = {"model": health["model"], "session_id": session_id, "task_id": created["id"],
                  "status": created["status"], "error": created["error"], "latency_ms": created["latency_ms"],
                  "element_count": len(scene["elements"]),
                  "committed_actions": sum(item["kind"] == "commit" for item in logs),
                  "child_agents": sorted({item["agent_id"] for item in logs if item["agent_id"].startswith("agent_")}),
                  "diagram_checks": checks}
        if created["status"] == "completed" and all(checks.values()):
            title = next(item for item in scene["elements"] if item["type"] == "text" and "transformer" in item.get("text", "").lower())
            request("PUT", f"/documents/{scene['id']}/scene", {
                "version": scene["version"], "elements": scene["elements"], "selection": [title["id"]],
            })
            edited = send("请把选中的标题文字改为“Transformer 模型结构”，文字颜色设为深蓝色 #1e40af，保持其他对象不变。")
            after = request("GET", f"/sessions/{session_id}")["document"]
            edit_logs = request("GET", f"/tasks/{edited['id']}/logs")
            updated = next(item for item in after["elements"] if item["id"] == title["id"])
            unaffected = {item["id"]: element_fingerprint(item) for item in scene["elements"] if item["id"] != title["id"]}
            edit_checks = {
                "same_object": len(after["elements"]) == len(scene["elements"]),
                "title_updated": updated.get("text") == "Transformer 模型结构" and updated["strokeColor"] == "#1e40af",
                "other_objects_preserved": unaffected == {item["id"]: element_fingerprint(item) for item in after["elements"] if item["id"] != title["id"]},
                "real_model_edit": any(item["kind"] == "llm_response" and item["agent_id"] == "main" for item in edit_logs),
            }
            commits = sum(item["kind"] == "commit" for item in edit_logs)
            for _ in range(commits):
                undone = request("POST", f"/documents/{scene['id']}/history", {"direction": "undo"})
            edit_checks["undo"] = commits > 0 and [element_fingerprint(item) for item in undone["elements"]] == [element_fingerprint(item) for item in scene["elements"]]
            for _ in range(commits):
                redone = request("POST", f"/documents/{scene['id']}/history", {"direction": "redo"})
            edit_checks["redo"] = commits > 0 and [element_fingerprint(item) for item in redone["elements"]] == [element_fingerprint(item) for item in after["elements"]]
            result.update(edit_status=edited["status"], edit_latency_ms=edited["latency_ms"], edit_checks=edit_checks)
        result["passed"] = created["status"] == "completed" and all(checks.values()) and result.get("edit_status") == "completed" and all(result.get("edit_checks", {}).values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

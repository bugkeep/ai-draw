import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from server.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("runs/portrait-evaluation.json"))
    parser.add_argument("--prompt", default="画一幅蒙娜丽莎素描图")
    parser.add_argument("--session-id")
    parser.add_argument("--task-id")
    parser.add_argument("--database", type=Path, help="Load the production ASGI app with an independent database")
    args = parser.parse_args()
    if bool(args.session_id) != bool(args.task_id):
        raise ValueError("Provide session-id and task-id together")
    if args.database:
        if args.database.resolve() == Path("runs/studio.sqlite3").resolve():
            raise ValueError("Use a separate evaluation database")
        os.environ["DRAW_AGENT_DB"] = str(args.database.resolve())
    with (TestClient(create_app()) if args.database else httpx.Client(base_url=args.url, timeout=30)) as client:
        if args.session_id:
            session_id, task_id = args.session_id, args.task_id
            if not task_id:
                raise ValueError("An existing session requires its task_id")
        else:
            response = client.post("/api/studio/sessions", json={"title": "蒙娜丽莎 · 连续素描验收"})
            response.raise_for_status()
            session_id = response.json()["id"]
            response = client.post(f"/api/studio/sessions/{session_id}/messages", json={"message": args.prompt})
            response.raise_for_status()
            task_id = response.json()["id"]
        print(json.dumps({"session_id": session_id, "task_id": task_id}, ensure_ascii=False), flush=True)
        last = None
        try:
            while True:
                response = client.get(f"/api/studio/tasks/{task_id}")
                response.raise_for_status()
                task = response.json()
                if task["session_id"] != session_id:
                    raise ValueError("The task belongs to another session")
                response = client.get(f"/api/studio/sessions/{session_id}")
                response.raise_for_status()
                session = response.json()
                count = len([item for item in session["document"]["elements"] if not item.get("isDeleted")])
                phase = (session.get("activity") or {}).get("phase")
                state = task["status"], count, phase
                if state != last:
                    print(json.dumps({"status": state[0], "elements": count, "phase": phase,
                                      "elapsed_seconds": round(time.time() - task["created_at"], 1)}), flush=True)
                    last = state
                if task["status"] in {"completed", "partial", "failed", "cancelled"}:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            client.post(f"/api/studio/tasks/{task_id}/cancel").raise_for_status()
            raise
        response = client.get(f"/api/studio/tasks/{task_id}/logs")
        response.raise_for_status()
        logs = response.json()
    document = session["document"]
    elements = [item for item in document["elements"] if not item.get("isDeleted")]
    named = lambda *words: [item for item in elements if any(
        word in item.get("customData", {}).get("name", "").casefold() for word in words)]
    groups = {"face": named("脸", "面部", "face", "jaw", "下颌"),
              "eyes": named("眼", "eye", "瞳"), "nose": named("鼻", "nose"),
              "mouth": named("唇", "嘴", "微笑", "mouth", "smile", "lip"),
              "hair": named("头发", "发丝", "发际", "发线", "hair"),
              "hands": named("手", "指", "hand", "finger"),
              "clothing": named("衣", "袖", "褶", "dress", "sleeve", "fold", "robe"),
              "landscape": named("背景", "山", "河", "landscape", "mountain", "river", "background", "hill", "shore")}
    checks = {f"named_{key}_geometry": any(item["type"] != "text" for item in value) for key, value in groups.items()}
    checks["two_eye_regions"] = len(groups["eyes"]) >= 2
    checks["editable_native_strokes"] = len([item for item in elements if item["type"] == "freedraw"]) >= 30
    checks["no_image_elements"] = all(item["type"] != "image" for item in elements)
    commits = [item for item in logs if item["kind"] == "commit"]
    calls = Counter(item["payload"]["name"] for item in logs if item["kind"] == "function_call")
    result = {"prompt": args.prompt, "session_id": session_id, "task_id": task_id,
              "database": str(args.database) if args.database else None,
              "status": task["status"], "error": task["error"], "summary": task["summary"],
              "latency_ms": task["latency_ms"], "element_count": len(elements),
              "first_canvas_update_ms": round((commits[0]["created_at"] - task["created_at"]) * 1000, 1) if commits else None,
              "model_responses": sum(item["kind"] == "llm_response" for item in logs),
              "model_retry_count": sum(item["kind"] == "model_retry" for item in logs),
              "tool_errors": [item["payload"] for item in logs if item["kind"] == "function_call_output"
                              and json.loads(item["payload"]["output"]).get("ok") is False],
              "tool_calls": dict(calls), "checks": checks,
              "tool_error_feedback_ids": sorted({call_id for item in logs if item["kind"] == "model_context"
                                                 for call_id in item["payload"].get("tool_error_ids", [])}),
              "structural_checks_passed": task["status"] == "completed" and all(checks.values()),
              "visual_acceptance": "pending_user_review"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".excalidraw").write_text(json.dumps(
        {"type": "excalidraw", "version": 2, "source": "draw-agent-studio",
         "elements": elements, "appState": {"viewBackgroundColor": "#ffffff"}, "files": {}},
        ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result["structural_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

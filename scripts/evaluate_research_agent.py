import argparse
import json
import os
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from server.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("runs/research-agent.json"))
    parser.add_argument("--session-id")
    parser.add_argument("--task-id")
    parser.add_argument("--database", type=Path, help="Run the production ASGI app in process with a separate database")
    args = parser.parse_args()
    if bool(args.session_id) != bool(args.task_id):
        raise ValueError("Provide session-id and task-id together")
    prompt = ("请让一个子 Agent 使用 search_references 查询栖息的小鸟如何收拢翅膀的画法，"
              "用 read_reference 进一步读取至少一个有用来源，并返回作画方案和来源链接。"
              "根据资料画一只站在树枝上的小鸟，保留翅膀的羽毛分组、脚爪和树枝。"
              "展示逐步作画，完成说明中附上参考来源链接。")
    if args.database:
        if args.database.resolve() == Path("runs/studio.sqlite3").resolve():
            raise ValueError("Use a separate evaluation database")
        os.environ["DRAW_AGENT_DB"] = str(args.database.resolve())
    with (TestClient(create_app()) if args.database else httpx.Client(base_url=args.url, timeout=30)) as client:
        if args.session_id:
            session_id, task_id = args.session_id, args.task_id
        else:
            response = client.post("/api/studio/sessions", json={"title": "Agent 联网绘图检查"})
            response.raise_for_status()
            session_id = response.json()["id"]
            response = client.post(f"/api/studio/sessions/{session_id}/messages", json={"message": prompt})
            response.raise_for_status()
            task_id = response.json()["id"]
        print(json.dumps({"session_id": session_id, "task_id": task_id}), flush=True)
        try:
            while True:
                response = client.get(f"/api/studio/tasks/{task_id}")
                response.raise_for_status()
                task = response.json()
                if task["status"] in {"completed", "failed", "cancelled", "partial"}:
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            client.post(f"/api/studio/tasks/{task_id}/cancel").raise_for_status()
            raise
        response = client.get(f"/api/studio/tasks/{task_id}/logs")
        response.raise_for_status()
        logs = response.json()
        calls = {item["payload"]["call_id"]: item for item in logs if item["kind"] == "function_call"}
        outputs = [(calls[item["payload"]["call_id"]], json.loads(item["payload"]["output"]))
                   for item in logs if item["kind"] == "function_call_output"]
        response = client.get(f"/api/studio/sessions/{session_id}")
        response.raise_for_status()
        session = response.json()
        elements = [item for item in session["document"]["elements"] if not item.get("isDeleted")]
        references = [source for call, output in outputs if call["payload"]["name"] == "search_references"
                      and output.get("ok") for source in output.get("sources", [])]
        checks = {
            "completed": task["status"] == "completed",
            "child_search": any(call["agent_id"].startswith("agent_") and output.get("ok")
                                for call, output in outputs if call["payload"]["name"] == "search_references"),
            "child_read": any(call["agent_id"].startswith("agent_") and output.get("ok")
                              for call, output in outputs if call["payload"]["name"] == "read_reference"),
            "verified_sources": bool(references) and all(source.get("url") and source.get("excerpts") for source in references),
            "native_drawing": bool(elements) and all(item["type"] != "image" for item in elements),
            "source_attribution": any(source["url"] in message["content"] or any(
                citation["url"] == source["url"] for citation in message.get("references", [])) for source in references
                                      for message in session["messages"] if message["role"] == "assistant"),
        }
        error_calls = {(call["agent_id"], call["payload"]["call_id"])
                       for call, output in outputs if output.get("ok") is False}
        fed_back = {(item["agent_id"], call_id) for item in logs if item["kind"] == "model_context"
                    for call_id in item["payload"].get("tool_error_ids", [])}
        checks["tool_errors_returned_to_owner"] = error_calls <= fed_back
        result = {"session_id": session_id, "task_id": task_id, "status": task["status"],
                  "database": str(args.database) if args.database else None,
                  "latency_ms": task["latency_ms"], "element_count": len(elements),
                  "checks": checks, "passed": all(checks.values()),
                  "sources": [{"title": source["title"], "url": source["url"]} for source in references],
                  "tool_error_count": len(error_calls), "visual_acceptance": "pending_user_review"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

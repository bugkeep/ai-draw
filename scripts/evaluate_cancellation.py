import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("runs/model-cancellation-evaluation.json"))
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=15) as client:
        def request(method, path, data=None):
            response = client.request(method, f"/api/studio{path}", json=data)
            response.raise_for_status()
            return response.json()

        if not request("GET", "/health")["provider_configured"]:
            raise RuntimeError("Configure a real model before running this evaluation")
        session = request("POST", "/sessions", {"title": "模型请求取消评测"})
        task = request("POST", f"/sessions/{session['id']}/messages", {"message": "绘制一个包含编码器和解码器的 Transformer 流程图"})
        while task["status"] == "queued":
            time.sleep(0.01)
            task = request("GET", f"/tasks/{task['id']}")
        time.sleep(0.5)
        before = request("GET", f"/tasks/{task['id']}")
        if before["status"] != "running":
            raise RuntimeError(f"Model task ended before cancellation: {before['status']}")
        started = time.perf_counter()
        request("POST", f"/tasks/{task['id']}/cancel")
        while task["status"] not in {"completed", "partial", "failed", "cancelled"}:
            task = request("GET", f"/tasks/{task['id']}")
            time.sleep(0.01)
        result = {"task_id": task["id"], "status": task["status"],
                  "cancel_latency_ms": round((time.perf_counter() - started) * 1000, 1),
                  "runtime_idle": not request("GET", "/settings")["busy"]}
        result["passed"] = result["status"] == "cancelled" and result["runtime_idle"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

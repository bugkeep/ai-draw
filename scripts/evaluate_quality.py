import argparse
import asyncio
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from server.app import create_app
from studio.renderer import CanvasRenderer
from studio.vision import scene_digest
from studio.quality import review_scene_key


async def render_final_document(document):
    renderer = CanvasRenderer()
    try:
        return (await renderer.render(document))['png']
    finally:
        await renderer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument('--database', type=Path, help='Run production ASGI in process with a separate database')
    parser.add_argument("--cases", default="")
    parser.add_argument("--dataset", type=Path, default=Path("evaluations/drawing-quality.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("runs/drawing-quality"))
    parser.add_argument("--recheck", action="store_true", help="Recheck saved evaluation tasks without requesting new drawings")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    implementation_files = [path for package in ('studio', 'server')
                            for path in sorted((root / package).rglob('*.py'))]
    implementation_files.extend(root / name for name in ('requirements.txt', 'requirements-dev.txt',
        'frontend-app/src/renderCanvas.js', 'frontend-app/package.json', 'frontend-app/package-lock.json',
        'scripts/evaluate_quality.py', 'scripts/verify_browser.py', 'frontend-app/scripts/quality_evaluation.js'))
    implementation = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in implementation_files}
    dataset_hash = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    cases = json.loads(args.dataset.read_text(encoding="utf-8"))["cases"]
    selected = set(args.cases.split(",")) if args.cases else {case["id"] for case in cases}
    if selected - {case["id"] for case in cases}:
        raise ValueError("Unknown evaluation case")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.recheck:
        for name in implementation:
            snapshot = args.output_dir / 'implementation' / name
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, snapshot)
    results = []
    if args.database:
        if args.database.resolve() == Path('runs/studio.sqlite3').resolve():
            raise ValueError('Use a separate evaluation database')
        os.environ['DRAW_AGENT_DB'] = str(args.database.resolve())
        client_context = TestClient(create_app())
    else:
        client_context = httpx.Client(base_url=args.url, timeout=30)
    with client_context as client:
        for case in cases:
            if case["id"] not in selected:
                continue
            if args.recheck:
                existing = json.loads((args.output_dir / f"{case['id']}.json").read_text(encoding="utf-8"))
                session_id, task_id = existing["session_id"], existing["task_id"]
            else:
                response = client.post("/api/studio/sessions", json={"title": f"绘图质量 · {case['id']}"})
                response.raise_for_status()
                session_id = response.json()["id"]
                response = client.post(f"/api/studio/sessions/{session_id}/messages", json={"message": case["prompt"]})
                response.raise_for_status()
                task_id = response.json()["id"]
            print(json.dumps({"case": case["id"], "session_id": session_id, "task_id": task_id}, ensure_ascii=False), flush=True)
            try:
                while True:
                    response = client.get(f"/api/studio/tasks/{task_id}")
                    response.raise_for_status()
                    task = response.json()
                    if task["status"] in {"completed", "partial", "failed", "cancelled"}:
                        break
                    time.sleep(0.5)
            except KeyboardInterrupt:
                client.post(f"/api/studio/tasks/{task_id}/cancel").raise_for_status()
                raise
            response = client.get(f"/api/studio/sessions/{session_id}")
            response.raise_for_status()
            scene = response.json()["document"]
            elements = [item for item in scene["elements"] if not item.get("isDeleted")]
            response = client.get(f"/api/studio/tasks/{task_id}/logs")
            response.raise_for_status()
            logs = response.json()
            final_hash = scene_digest(scene)
            full_images = [item['payload'] for item in logs if item['kind'] == 'canvas_observation'
                           and item['agent_id'] == 'main' and item['payload'].get('region') is None
                           and item['payload'].get('scope', 'shared') == 'shared'
                           and item['payload']['scene_hash'] == final_hash]
            contexts = [item['payload'] for item in logs if item['agent_id'] == 'main' and item['kind'] == 'model_context']
            delivered_ids = {observation_id for context in contexts for observation_id in context.get('image_observation_ids', [])}
            final_observed = any(item['observation_id'] in delivered_ids for item in full_images)
            (args.output_dir / f"{case['id']}.png").write_bytes(asyncio.run(render_final_document(scene)))
            checks = {key: any(any(word in (item.get("customData", {}).get("name", "") + " " + item.get("text", "")).casefold()
                                           for word in words) for item in elements
                               if item["type"] != "text" or case.get("min_arrows") or key == "sign")
                      for key, words in case["components"].items()}
            if case.get("min_arrows"):
                nodes = [item for item in elements if item["type"] in {"rectangle", "ellipse", "diamond"}]
                checks["labels_inside_nodes"] = all(any(
                    any(word in label.get("text", "").casefold() for word in words)
                    and any(node["x"] <= label["x"] and node["y"] <= label["y"]
                            and label["x"] + label["width"] <= node["x"] + node["width"] + 3
                            and label["y"] + label["height"] <= node["y"] + node["height"] + 3 for node in nodes)
                    for label in elements if label["type"] == "text") for words in case["components"].values())
            checks["native_geometry"] = bool(elements) and all(item["type"] in {
                "freedraw", "line", "arrow", "ellipse", "rectangle", "diamond", "text"} for item in elements)
            checks["arrows"] = sum(item["type"] == "arrow" for item in elements) >= case.get("min_arrows", 0)
            checks['final_model_canvas_inspected'] = not contexts or final_observed
            quality = [item['payload']['result'] for item in logs if item['kind'] == 'quality_review'
                       and item['payload']['result']['scene_key'] == review_scene_key(scene)]
            quality = quality[-1] if quality else None
            checks['independent_visual_review_passed'] = not contexts or bool(quality and quality['ok'])
            commits = [item for item in logs if item["kind"] == "commit"]
            result = {"case": case["id"], "prompt": case["prompt"], "session_id": session_id, "task_id": task_id,
                      'implementation_sha256': existing.get('implementation_sha256') if args.recheck else implementation,
                      'dataset_sha256': existing.get('dataset_sha256') if args.recheck else dataset_hash,
                      'evaluation_implementation_sha256': implementation, 'evaluation_dataset_sha256': dataset_hash,
                      "status": task["status"], "error": task["error"], "element_count": len(elements),
                      "latency_ms": task["latency_ms"], "structural_checks": checks,
                      "first_canvas_update_ms": round((commits[0]["created_at"] - task["created_at"]) * 1000, 1) if commits else None,
                      "model_retry_count": sum(item["kind"] == "model_retry" for item in logs),
                      'independent_visual_review': quality,
                      'image_observation_count': sum(item['kind'] == 'canvas_observation' for item in logs),
                      'final_shared_scene_hash': final_hash,
                      'final_model_canvas_observed': final_observed if contexts else None,
                      'model_context_count': len(contexts),
                      'png_source': 'rendered_final_shared_native_document',
                      'result_preview_count': sum(len(context.get('result_observation_ids', [])) for context in contexts),
                      "tool_error_count": sum(item["kind"] == "function_call_output" and
                                              json.loads(item["payload"]["output"]).get("ok") is False for item in logs),
                      "structural_checks_passed": task["status"] == "completed" and all(checks.values()),
                      "visual_rubric": case["visual_rubric"], "visual_acceptance": "pending_user_review"}
            if args.recheck:
                result['prompt'] = existing.get('prompt', case['prompt'])
            (args.output_dir / f"{case['id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            (args.output_dir / f"{case['id']}.excalidraw").write_text(json.dumps({
                "type": "excalidraw", "version": 2, "source": "draw-agent-studio", "elements": elements,
                "appState": {"viewBackgroundColor": "#ffffff"}, "files": {},
            }, ensure_ascii=False), encoding="utf-8")
            results.append(result)
            print(json.dumps({key: result[key] for key in ('case', 'session_id', 'task_id', 'status', 'error',
                'element_count', 'latency_ms', 'first_canvas_update_ms', 'model_context_count',
                'structural_checks_passed')}, ensure_ascii=False), flush=True)
            (args.output_dir / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(result["structural_checks_passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

import argparse
import asyncio
import json
import sqlite3
import time
from pathlib import Path

from studio.actions import element_fingerprint
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore


def stroke_timings(rows, task_id):
    starts, elapsed, frames, point_counts = {}, [], {}, {}
    for row in rows:
        payload = json.loads(row["payload"])
        if row["type"] == "stroke_progress" and payload["task_id"] == task_id:
            element_id = payload["element"]["id"]
            starts.setdefault(element_id, row["created_at"])
            frames[element_id] = frames.get(element_id, 0) + 1
            point_counts.setdefault(element_id, []).append(len(payload["element"]["points"]))
        elif row["type"] == "scene" and payload.get("source") == "agent" and payload.get("action") == "create_element":
            element_id = payload["document"]["elements"][-1]["id"]
            if element_id in starts:
                elapsed.append((row["created_at"] - starts.pop(element_id)) * 1000)
    return {"stroke_count": len(elapsed), "duration_ms": round(sum(elapsed), 1),
            "progress_frames": sum(frames.values()),
            "progress_ordered": all(counts == sorted(counts) for counts in point_counts.values())}


async def evaluate(args):
    if args.database.resolve() in {args.source_database.resolve(), Path("runs/studio.sqlite3").resolve()}:
        raise ValueError("Use a separate playback database")
    source = sqlite3.connect(f"file:{args.source_database.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        task = source.execute("SELECT * FROM tasks WHERE id=?", (args.task_id,)).fetchone()
        if task is None or task["status"] != "completed":
            raise ValueError("Choose a completed actual drawing task")
        document = source.execute("SELECT d.* FROM documents d JOIN sessions s ON s.document_id=d.id WHERE s.id=?",
                                  (task["session_id"],)).fetchone()
        results = {row["id"]: json.loads(row["actions"]) for row in source.execute(
            "SELECT id,actions FROM drawing_results WHERE task_id=?", (args.task_id,))}
        prepared = []
        for row in source.execute("SELECT round_number,payload FROM tool_logs WHERE task_id=? AND kind='commit' ORDER BY id", (args.task_id,)):
            receipt = json.loads(row["payload"])
            prepared.append(results[receipt["result_id"]][row["round_number"] - 1])
        if not prepared or any(item["action"]["kind"] != "create_element" for item in prepared):
            raise ValueError("Choose a drawing task that creates a new composition")
        source_events = source.execute("SELECT type,payload,created_at FROM events WHERE document_id=? ORDER BY seq", (document["id"],)).fetchall()
        source_timing = stroke_timings(source_events, args.task_id)
        expected = json.loads(document["elements"])
    finally:
        source.close()
    store = StudioStore(args.database)
    runtime = DrawingRuntime(store, ModelSettings(store))
    try:
        session = store.create_session("实际模型作品回放")
        task = store.create_task(session["id"])
        store.update_task(task["id"], "running")
        result = store.agents.create_direct_result(session["id"], task["id"], "playback", prepared, "实际模型作品")
        started = time.perf_counter()
        await runtime.apply_result(task["id"], session["id"], session["document"]["id"], result["id"])
        latency = (time.perf_counter() - started) * 1000
        actual = store.get_document(session["document"]["id"])["elements"]
        rows = store.connection.execute("SELECT type,payload,created_at FROM events WHERE document_id=? ORDER BY seq", (session["document"]["id"],)).fetchall()
        playback = stroke_timings(rows, task["id"])
        checks = {
            "same_objects_and_geometry": [element_fingerprint(item) for item in actual] == [element_fingerprint(item) for item in expected],
            "every_stroke_has_progress": playback["stroke_count"] == sum(item["type"] == "freedraw" for item in actual),
            "ordered_progress": playback["progress_ordered"],
            "undo": len(store.history_step(session["document"]["id"], "undo")["elements"]) == len(actual) - 1,
            "redo": store.history_step(session["document"]["id"], "redo")["elements"] == actual,
        }
        store.update_task(task["id"], "completed" if all(checks.values()) else "failed")
        return {"source_task_id": args.task_id, "session_id": session["id"], "task_id": task["id"],
                "element_count": len(actual), "latency_ms": round(latency, 1), "source_strokes": source_timing,
                "playback_strokes": playback, "checks": checks, "passed": all(checks.values()),
                "model_requests": 0, "mode": "actual_saved_model_actions"}
    finally:
        await runtime.close()
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--database", type=Path, default=Path("runs/playback-evaluation.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("runs/playback-evaluation.json"))
    args = parser.parse_args()
    result = asyncio.run(evaluate(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

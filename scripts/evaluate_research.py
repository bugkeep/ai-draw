import argparse
import asyncio
import json
from pathlib import Path

from studio.research import DrawingResearch, public_url
from studio.store import StudioStore


async def evaluate(database: Path):
    store = StudioStore(database)
    session = store.create_session("实际绘图资料检索")
    task = store.create_task(session["id"])
    research = DrawingResearch(store)
    cases = [
        {"query": "how to draw mountains pencil step by step", "subject_terms": ["mountain"], "goal": "山水的远近层次、山体轮廓与阴影"},
        {"query": "how to draw hands holding a cup tutorial", "subject_terms": ["hand", "cup"], "goal": "双手握住杯子的结构与遮挡"},
    ]
    reports = []
    try:
        for case in cases:
            result = await research.call(task["id"], "research-evaluation", "search_references", case)
            reports.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if result.get("ok"):
                source = result["sources"][0]
                focused = await research.call(task["id"], "research-evaluation", "read_reference", {
                    "reference_id": source["reference_id"], "focus_terms": case["subject_terms"],
                })
                if not focused.get("ok"):
                    raise ValueError("A selected source could not be read")
                unrelated = await research.call(task["id"], "research-evaluation", "read_reference", {
                    "reference_id": source["reference_id"], "focus_terms": ["quasar spectroscopy"],
                })
                result["unrelated_focus_rejected"] = unrelated.get("ok") is False
                other = store.create_session("独立画布")
                other_task = store.create_task(other["id"])
                try:
                    await research.call(other_task["id"], "research-evaluation", "read_reference", {
                        "reference_id": source["reference_id"], "focus_terms": case["subject_terms"],
                    })
                except ValueError:
                    result["canvas_isolation"] = True
                else:
                    raise ValueError("Reference data crossed canvas ownership")
                cached = await research.call(task["id"], "research-evaluation", "search_references", case)
                result["cache_reused"] = cached.get("cached") is True
        try:
            await public_url("http://127.0.0.1:8000/")
        except ValueError:
            private_address_rejected = True
        else:
            private_address_rejected = False
        return {"cases": reports, "private_address_rejected": private_address_rejected,
                "passed": all(item.get("ok") and item.get("canvas_isolation") and item.get("cache_reused")
                              and item.get("unrelated_focus_rejected")
                              for item in reports) and private_address_rejected}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("runs/research-evaluation.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("runs/research-evaluation.json"))
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.database))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "private_address_rejected": result["private_address_rejected"]}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

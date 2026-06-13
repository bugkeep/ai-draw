import os
import json
import time

RUNS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))


class TaskManager:
    """Synchronous file-based CRUD for agent planning tasks.

    Each run's task data lives in ``runs/<run_id>/.tasks/<id>.json``,
    fully isolated from other runs.  No async, no EventBus, no state
    machine — pure file I/O.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._tasks_dir = os.path.join(RUNS_DIR, run_id, ".tasks")
        os.makedirs(self._tasks_dir, exist_ok=True)

    def _task_path(self, task_id: int) -> str:
        return os.path.join(self._tasks_dir, f"{task_id}.json")

    def _next_id(self) -> int:
        existing = []
        try:
            for fname in os.listdir(self._tasks_dir):
                if fname.endswith(".json") and fname[0].isdigit():
                    existing.append(int(fname.replace(".json", "")))
        except FileNotFoundError:
            pass
        return max(existing) + 1 if existing else 1

    def create(self, subject: str, description: str = "",
               blocked_by: list[int] | None = None) -> dict:
        task_id = self._next_id()
        now = time.time()
        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blocked_by": blocked_by or [],
            "created_at": now,
            "updated_at": now,
        }
        with open(self._task_path(task_id), "w", encoding="utf-8") as f:
            json.dump(task, f, ensure_ascii=False, indent=2)
        return task

    def update(self, task_id: int, **kwargs) -> dict | None:
        task = self.get(task_id)
        if task is None:
            return None
        valid_keys = {"subject", "description", "status", "blocked_by"}
        changed = False
        for k, v in kwargs.items():
            if k in valid_keys and task.get(k) != v:
                task[k] = v
                changed = True
        if changed:
            task["updated_at"] = time.time()
            with open(self._task_path(task_id), "w", encoding="utf-8") as f:
                json.dump(task, f, ensure_ascii=False, indent=2)
        return task

    def get(self, task_id: int) -> dict | None:
        path = self._task_path(task_id)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def list(self, status: str | None = None) -> list[dict]:
        tasks = []
        try:
            for fname in sorted(os.listdir(self._tasks_dir)):
                if fname.endswith(".json") and fname[0].isdigit():
                    with open(os.path.join(self._tasks_dir, fname), encoding="utf-8") as f:
                        task = json.load(f)
                        if status is None or task.get("status") == status:
                            tasks.append(task)
        except FileNotFoundError:
            pass
        return sorted(tasks, key=lambda t: t.get("id", 0))

    def delete(self, task_id: int) -> bool:
        path = self._task_path(task_id)
        if not os.path.isfile(path):
            return False
        os.remove(path)
        return True

import os
import json
import time

RUNS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))

VALID_STATUSES = {"pending", "in_progress", "completed", "failed", "cancelled"}


class TaskManager:
    """Synchronous file-based CRUD for agent planning tasks.

    Each run's task data lives in ``runs/<run_id>/.tasks/<id>.json``,
    fully isolated from other runs.  No async, no EventBus, no state
    machine — pure file I/O.

    Supports optimistic locking via ``revision``, agent ownership with
    ``assigned_agent`` / ``lease_expires_at``, and automatic dependency
    clearing when a task is marked ``completed``.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._tasks_dir = os.path.join(RUNS_DIR, run_id, ".tasks")
        os.makedirs(self._tasks_dir, exist_ok=True)

    # ── internal helpers ──────────────────────────────────────────────

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

    # ── CRUD ──────────────────────────────────────────────────────────

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
            "assigned_agent": "",
            "lease_expires_at": 0.0,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        with open(self._task_path(task_id), "w", encoding="utf-8") as f:
            json.dump(task, f, ensure_ascii=False, indent=2)
        return task

    def update(self, task_id: int, **kwargs) -> dict:
        """Update a task with optional revision-based conflict detection.

        Returns ``{"ok": True, "task": {...}}`` on success, or
        ``{"ok": False, "error": "...", "current_revision": N, "current_owner": "..."}``
        on conflict / not-found.
        """
        task = self.get(task_id)
        if task is None:
            return {"ok": False, "error": "NOT_FOUND"}

        # Extract special params
        provided_revision = kwargs.pop("revision", None)
        lease_seconds = kwargs.pop("lease_seconds", None)

        # Revision check (optimistic locking)
        if provided_revision is not None and provided_revision != task.get("revision"):
            return {
                "ok": False,
                "error": "TASK_CONFLICT",
                "current_revision": task["revision"],
                "current_owner": task.get("assigned_agent", ""),
            }

        valid_keys = {"subject", "description", "status", "blocked_by",
                      "assigned_agent"}
        changed = False

        for k, v in kwargs.items():
            if k not in valid_keys:
                continue
            if k == "status" and v not in VALID_STATUSES:
                continue
            if task.get(k) != v:
                task[k] = v
                changed = True

        # Lease extension (heartbeat)
        if lease_seconds is not None:
            task["lease_expires_at"] = time.time() + lease_seconds
            changed = True

        if changed:
            task["revision"] = task.get("revision", 0) + 1
            task["updated_at"] = time.time()
            with open(self._task_path(task_id), "w", encoding="utf-8") as f:
                json.dump(task, f, ensure_ascii=False, indent=2)

            if task.get("status") == "completed":
                self._clear_dependency(task_id)

        return {"ok": True, "task": task}

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

    # ── dependency auto-clear ─────────────────────────────────────────

    def _clear_dependency(self, completed_id: int):
        """Remove ``completed_id`` from all other tasks' ``blocked_by``."""
        try:
            for fname in os.listdir(self._tasks_dir):
                if not (fname.endswith(".json") and fname[0].isdigit()):
                    continue
                path = os.path.join(self._tasks_dir, fname)
                with open(path, encoding="utf-8") as f:
                    task = json.load(f)
                blocked = task.get("blocked_by", [])
                if completed_id not in blocked:
                    continue
                task["blocked_by"] = [b for b in blocked if b != completed_id]
                task["updated_at"] = time.time()
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(task, f, ensure_ascii=False, indent=2)
        except FileNotFoundError:
            pass

    # ── format helpers ────────────────────────────────────────────────

    STATUS_SYMBOL = {
        "pending": " ",       # READY section
        "in_progress": ">",
        "completed": "x",
        "failed": "!",
        "cancelled": "-",
    }

    def _is_blocked(self, task: dict) -> bool:
        """A pending task is blocked if it still has unresolved deps."""
        blocked = task.get("blocked_by", [])
        if not blocked:
            return False
        # Each dep should already have been auto-cleared, but be defensive.
        return True

    def format_list(self) -> str:
        """Return a compact sectioned view of all tasks (for LLM consumption)."""
        tasks = self.list()

        buckets = {"READY": [], "RUNNING": [], "BLOCKED": [],
                    "DONE": [], "FAILED": [], "CANCELLED": []}

        for t in tasks:
            status = t.get("status", "pending")
            if status == "completed":
                buckets["DONE"].append(t)
            elif status == "in_progress":
                buckets["RUNNING"].append(t)
            elif status == "failed":
                buckets["FAILED"].append(t)
            elif status == "cancelled":
                buckets["CANCELLED"].append(t)
            elif status == "pending" and self._is_blocked(t):
                buckets["BLOCKED"].append(t)
            else:
                buckets["READY"].append(t)

        lines = []
        for section in ("READY", "RUNNING", "BLOCKED", "DONE", "FAILED", "CANCELLED"):
            section_tasks = buckets[section]
            if not section_tasks:
                continue
            lines.append(section)
            for t in section_tasks:
                tid = t.get("id", "?")
                subject = t.get("subject", "")

                if section == "BLOCKED":
                    sym = "#"
                    deps = ",".join(str(d) for d in t.get("blocked_by", []))
                    line = f"[{sym}] T{tid} {subject} <- T{deps}"
                else:
                    sym = self.STATUS_SYMBOL.get(t.get("status", ""), " ")
                    line = f"[{sym}] T{tid} {subject}"

                lines.append(line)

                if section == "RUNNING":
                    owner = t.get("assigned_agent", "")
                    lease = t.get("lease_expires_at", 0)
                    info_parts = []
                    if owner:
                        info_parts.append(f"owner={owner}")
                    if lease:
                        remaining = max(0, int(lease - time.time()))
                        info_parts.append(f"lease={remaining}s")
                    if info_parts:
                        lines.append(f"    {' '.join(info_parts)}")

            lines.append("")

        return "\n".join(lines).strip()

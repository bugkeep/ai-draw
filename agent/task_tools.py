import time
from tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from agent.task_manager import TaskManager


def _parse_blocked_by(raw: str | list | None) -> list[int]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [int(x) for x in raw]
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


class TaskCreateTool(BaseTool):
    """Create a new planning task."""

    def __init__(self, task_manager: TaskManager):
        self._tm = task_manager

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="task_create",
            description="Create a new task for the current drawing plan",
            parameters=[
                ToolParameter(name="subject", type="string",
                              description="Task subject, e.g. 'draw background'",
                              required=True),
                ToolParameter(name="description", type="string",
                              description="Detailed description of what this task involves"),
                ToolParameter(name="blocked_by", type="string",
                              description="Comma-separated task IDs this depends on, e.g. '1,3'"),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        subject = kwargs.get("subject", "")
        description = kwargs.get("description", "")
        blocked_by = _parse_blocked_by(kwargs.get("blocked_by"))
        task = self._tm.create(subject, description, blocked_by)
        return ToolResult(
            data=task,
            description=f"Created task #{task['id']}: {task['subject']}",
        )


class TaskUpdateTool(BaseTool):
    """Update an existing task with revision-based conflict detection."""

    def __init__(self, task_manager: TaskManager):
        self._tm = task_manager

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="task_update",
            description=(
                "Update a task's status, content, owner, or renew its lease. "
                "Include ``revision`` (the number you last read) for safe "
                "conflict-free updates.  Use ``lease_seconds`` as heartbeat."
            ),
            parameters=[
                ToolParameter(name="task_id", type="integer",
                              description="Task ID to update", required=True),
                ToolParameter(name="status", type="string",
                              description="New status",
                              enum=["pending", "in_progress", "completed",
                                    "failed", "cancelled"]),
                ToolParameter(name="subject", type="string",
                              description="New subject"),
                ToolParameter(name="description", type="string",
                              description="New description"),
                ToolParameter(name="blocked_by", type="string",
                              description="Comma-separated dependency task IDs, e.g. '1,3'"),
                ToolParameter(name="assigned_agent", type="string",
                              description="Agent name that owns this task (e.g. 'drawing-agent-1')"),
                ToolParameter(name="revision", type="integer",
                              description="Expected revision for conflict check (omit to skip check)"),
                ToolParameter(name="lease_seconds", type="integer",
                              description="Extend lease by N seconds (heartbeat)"),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        task_id = kwargs.get("task_id")
        if task_id is None:
            return ToolResult(is_error=True, error="task_id is required")

        updates = {}
        for key in ("subject", "description", "status"):
            if key in kwargs and kwargs[key] is not None:
                updates[key] = kwargs[key]
        if "blocked_by" in kwargs and kwargs["blocked_by"] is not None:
            updates["blocked_by"] = _parse_blocked_by(kwargs["blocked_by"])
        if "assigned_agent" in kwargs and kwargs["assigned_agent"] is not None:
            updates["assigned_agent"] = kwargs["assigned_agent"]
        if "revision" in kwargs and kwargs["revision"] is not None:
            updates["revision"] = kwargs["revision"]
        if "lease_seconds" in kwargs and kwargs["lease_seconds"] is not None:
            updates["lease_seconds"] = kwargs["lease_seconds"]

        result = self._tm.update(task_id, **updates)

        if not result["ok"]:
            if result["error"] == "TASK_CONFLICT":
                return ToolResult(
                    is_error=True,
                    error=(
                        f"TASK_CONFLICT: owned by {result['current_owner']} "
                        f"at revision {result['current_revision']}"
                    ),
                    data={
                        "current_revision": result["current_revision"],
                        "current_owner": result["current_owner"],
                    },
                )
            return ToolResult(is_error=True, error=result["error"])

        task = result["task"]
        return ToolResult(
            data=task,
            description=f"Updated task #{task_id}: {task['status']}",
        )


class TaskListTool(BaseTool):
    """List all tasks in a compact sectioned view."""

    def __init__(self, task_manager: TaskManager):
        self._tm = task_manager

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="task_list",
            description="List all tasks in sectioned format (READY / RUNNING / BLOCKED / DONE / FAILED / CANCELLED)",
            parameters=[],
        )

    def execute(self, **kwargs) -> ToolResult:
        formatted = self._tm.format_list()
        tasks = self._tm.list()
        return ToolResult(
            data=tasks,
            description=formatted or "No tasks yet",
        )


class TaskGetTool(BaseTool):
    """Get details of a single task by ID."""

    def __init__(self, task_manager: TaskManager):
        self._tm = task_manager

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="task_get",
            description="Get full details of a specific task by ID",
            parameters=[
                ToolParameter(name="task_id", type="integer",
                              description="Task ID", required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        task_id = kwargs.get("task_id")
        if task_id is None:
            return ToolResult(is_error=True, error="task_id is required")
        task = self._tm.get(task_id)
        if task is None:
            return ToolResult(is_error=True, error=f"Task #{task_id} not found")
        return ToolResult(
            data=task,
            description=f"Task #{task_id}: {task['subject']} [{task['status']}]",
        )


TASK_TOOLS = [TaskCreateTool, TaskUpdateTool, TaskListTool, TaskGetTool]

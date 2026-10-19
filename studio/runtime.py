import asyncio
import math
import time
import uuid
from typing import Any

from .actions import create_native_element, element_fingerprint, validate_action
from .agents import AgentSettings, DrawingAgents
from .fast_path import fast_actions
from .store import StudioStore, VersionConflict


class DrawingRuntime:
    def __init__(self, store: StudioStore):
        self.store = store
        self.running: dict[str, asyncio.Task] = {}
        self.document_locks: dict[str, asyncio.Lock] = {}
        self.cancel_requested: set[str] = set()

    def submit(self, session_id: str, message: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        task = self.store.create_task(session_id)
        self.store.add_message(session_id, "user", message, task["id"])
        document_id = session["document"]["id"]
        self.store.publish(document_id, "task", {"task_id": task["id"], "status": "queued"})
        self.running[task["id"]] = asyncio.create_task(
            self._run(task["id"], session_id, document_id, message)
        )
        return task

    def cancel(self, task_id: str) -> None:
        task = self.running.get(task_id)
        if task is None:
            raise KeyError(f"Running task not found: {task_id}")
        if self.store.get_task(task_id)["status"] == "queued":
            self.cancel_requested.add(task_id)
        else:
            task.cancel()

    async def close(self) -> None:
        for task in self.running.values():
            task.cancel()
        if self.running:
            await asyncio.gather(*self.running.values(), return_exceptions=True)

    def _status(self, task_id: str, document_id: str, status: str,
                summary: str = "", error: str = "", latency_ms: float = 0) -> None:
        self.store.update_task(task_id, status, summary, error, latency_ms)
        self.store.publish(document_id, "task", {
            "task_id": task_id, "status": status, "summary": summary,
            "error": error, "latency_ms": round(latency_ms, 1),
        })

    async def _run(self, task_id: str, session_id: str, document_id: str, message: str) -> None:
        started = time.perf_counter()
        lock = self.document_locks.setdefault(document_id, asyncio.Lock())
        try:
            if task_id in self.cancel_requested:
                raise asyncio.CancelledError
            async with lock:
                if task_id in self.cancel_requested:
                    raise asyncio.CancelledError
                self._status(task_id, document_id, "running")
                partial = False
                if message.strip().lower() in {"撤销", "撤销上一步", "undo"}:
                    self.store.history_step(document_id, "undo")
                    reply = "已撤销上一步。"
                elif message.strip().lower() in {"重做", "恢复上一步", "redo"}:
                    self.store.history_step(document_id, "redo")
                    reply = "已恢复上一步。"
                else:
                    quick = fast_actions(message, self.store.get_document(document_id))
                    if quick:
                        actions, reply = quick
                        await self._commit_prepared(task_id, document_id, [
                            {"action": validate_action(action), "expected_elements": None} for action in actions
                        ])
                    else:
                        settings = AgentSettings.from_environment()
                        agents = DrawingAgents(self.store, settings)
                        try:
                            reply, partial = await self._draw(agents, task_id, session_id, document_id, message)
                        finally:
                            await agents.close()
                assistant_message = self.store.add_message(session_id, "assistant", reply, task_id)
                self.store.publish(document_id, "message", assistant_message)
                self._status(task_id, document_id, "partial" if partial else "completed", reply,
                             latency_ms=(time.perf_counter() - started) * 1000)
        except asyncio.CancelledError:
            self.store.publish(document_id, "scene", {"document": self.store.get_document(document_id), "source": "cancel"})
            self._status(task_id, document_id, "cancelled", "绘图已停止。",
                         latency_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:
            self.store.log_tool(task_id, "runtime", 0, "error", {"type": type(exc).__name__, "message": str(exc)})
            self.store.publish(document_id, "scene", {"document": self.store.get_document(document_id), "source": "error"})
            self._status(task_id, document_id, "failed", error=str(exc),
                         latency_ms=(time.perf_counter() - started) * 1000)
        finally:
            self.cancel_requested.discard(task_id)
            self.running.pop(task_id, None)

    async def _draw(self, agents: DrawingAgents, task_id: str, session_id: str,
                    document_id: str, message: str) -> tuple[str, bool]:
        session = self.store.get_session(session_id)
        base_document = session["document"]
        plan = await agents.plan(task_id, message, base_document, session["messages"])
        self.store.publish(document_id, "phase", {"task_id": task_id, "phase": "planned",
                                                   "delegations": len(plan.delegations)})
        reports: list[dict[str, Any]] = []
        if plan.delegations:
            results = await asyncio.gather(
                *(agents.child(task_id, delegation, base_document) for delegation in plan.delegations),
                return_exceptions=True,
            )
            retry_indices = [index for index, result in enumerate(results) if isinstance(result, Exception)]
            if retry_indices:
                self.store.publish(document_id, "phase", {"task_id": task_id, "phase": "recovering",
                                                           "count": len(retry_indices)})
                self.store.log_tool(task_id, "main", 0, "retry_children", {
                    "ids": [plan.delegations[index].id for index in retry_indices],
                    "model": agents.settings.recovery_model,
                })
                retries = await asyncio.gather(
                    *(agents.child(task_id, plan.delegations[index], base_document,
                                   model=agents.settings.recovery_model) for index in retry_indices),
                    return_exceptions=True,
                )
                results = list(results)
                for index, retry in zip(retry_indices, retries):
                    results[index] = retry
            for delegation, result in zip(plan.delegations, results):
                if isinstance(result, Exception):
                    report = {"id": delegation.id, "goal": delegation.goal, "summary": "",
                              "actions": [], "success": False, "error": str(result)}
                else:
                    report = result
                reports.append(report)
                self.store.log_tool(task_id, "main", 0, "child_report", report)
                self.store.publish(document_id, "phase", {"task_id": task_id, "phase": "child_ready",
                                                           "child_id": delegation.id, "success": report["success"]})
        accepted_ids: list[str] = []
        review_reply = ""
        if reports:
            accepted_ids, review_reply = await agents.review(task_id, message, reports)
        prepared: list[dict[str, Any]] = []
        for action in plan.actions:
            prepared.append({"action": validate_action(action), "expected_elements": None})
        by_id = {report["id"]: report for report in reports}
        for child_id in accepted_ids:
            prepared.extend(by_id[child_id]["actions"])
        if not prepared:
            raise ValueError("No drawing actions passed main agent review")
        await self._commit_prepared(task_id, document_id, prepared)
        failed_goals = [report["id"] for report in reports if not report["success"]]
        if failed_goals:
            return f"已完成部分绘图，以下部分需要重试：{', '.join(failed_goals)}。", True
        return review_reply or plan.reply or f"已完成 {len(prepared)} 个绘图动作。", False

    async def _commit_prepared(self, task_id: str, document_id: str, prepared: list[dict[str, Any]]) -> None:
        total = len(prepared)
        for index, item in enumerate(prepared, 1):
            action = validate_action(item["action"])
            if action["kind"] == "create_element" and "element_id" not in action:
                action["element_id"] = uuid.uuid4().hex[:16]
            if item["expected_elements"] is None:
                current = self.store.get_document(document_id)
                current_by_id = {element["id"]: element for element in current["elements"]}
                expected = {element_id: {"version": int(current_by_id[element_id].get("version", 1)),
                                         "fingerprint": element_fingerprint(current_by_id[element_id])}
                            for element_id in action["args"].get("ids", []) if element_id in current_by_id}
            else:
                expected = item["expected_elements"]
            if action["kind"] == "create_element" and action["args"]["type"] == "freedraw":
                await self._animate_stroke(task_id, document_id, action)
            outcome = self.store.commit_action(document_id, action, expected)
            self.store.log_tool(task_id, "main", index, "commit", {
                "kind": action["kind"], "changed_ids": outcome["changed_ids"],
                "document_version": outcome["document"]["version"],
            })
            self.store.publish(document_id, "phase", {"task_id": task_id, "phase": "drawing",
                                                       "completed": index, "total": total})
            if total > 1:
                await asyncio.sleep(0.09)

    async def _animate_stroke(self, task_id: str, document_id: str, action: dict[str, Any]) -> None:
        element = create_native_element(action["args"], action.get("element_id"))
        points = element["points"]
        steps = min(16, max(3, math.ceil(len(points) / 3)))
        for frame in range(1, steps + 1):
            count = max(2, math.ceil(len(points) * frame / steps))
            partial = {**element, "points": points[:count], "pressures": element["pressures"][:count],
                       "width": max(point[0] for point in points[:count]),
                       "height": max(point[1] for point in points[:count])}
            self.store.publish(document_id, "stroke_progress", {"task_id": task_id, "element": partial})
            await asyncio.sleep(0.035)

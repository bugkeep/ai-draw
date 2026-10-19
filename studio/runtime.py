import asyncio
import json
import math
import time
import uuid
from typing import Any

from .actions import create_native_element, element_fingerprint, validate_action
from .agent_manager import AgentManager
from .agent_store import stable_id
from .agent_tools import CHILD_TOOL_NAMES, validate_tool
from .agents import DrawingAgents, prepare_drawing_tool, recent_tool_exchanges
from .errors import validation_summary
from .fast_path import fast_actions
from .settings import ModelSettings
from .store import StudioStore, VersionConflict


class DrawingRuntime:
    def __init__(self, store: StudioStore, settings: ModelSettings):
        self.store = store
        self.settings = settings
        self.running: dict[str, asyncio.Task] = {}
        self.document_locks: dict[str, asyncio.Lock] = {}
        self.cancel_requested: set[str] = set()
        self.children = AgentManager(store, settings)

    @property
    def busy(self) -> bool:
        return bool(self.running) or self.children.busy

    def session_busy(self, session_id: str) -> bool:
        return (any(self.store.get_task(task_id)["session_id"] == session_id for task_id in self.running)
                or self.children.session_busy(session_id))

    def submit(self, session_id: str, message: str) -> dict[str, Any]:
        document_id = self.store.session_document_id(session_id)
        task = self.store.create_task(session_id)
        self.store.add_message(session_id, "user", message, task["id"])
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
        await self.children.close()

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
                        agents = DrawingAgents(self.store, self.settings.load())
                        reply, partial = await self._draw(agents, task_id, session_id, document_id, message)
                assistant_message = self.store.add_message(session_id, "assistant", reply, task_id)
                self.store.publish(document_id, "message", assistant_message)
                self._status(task_id, document_id, "partial" if partial else "completed", reply,
                             latency_ms=(time.perf_counter() - started) * 1000)
        except asyncio.CancelledError:
            await self.children.close_task(task_id)
            self.store.publish(document_id, "scene", {"document": self.store.get_document(document_id), "source": "cancel"})
            self._status(task_id, document_id, "cancelled", "绘图已停止。",
                         latency_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:
            await self.children.close_task(task_id)
            self.store.log_tool(task_id, "runtime", 0, "error", {"type": type(exc).__name__, "message": str(exc)})
            self.store.publish(document_id, "scene", {"document": self.store.get_document(document_id), "source": "error"})
            self._status(task_id, document_id, "failed", error=validation_summary(exc),
                         latency_ms=(time.perf_counter() - started) * 1000)
        finally:
            await self.children.close_task(task_id)
            self.cancel_requested.discard(task_id)
            self.running.pop(task_id, None)

    async def _draw(self, agents: DrawingAgents, task_id: str, session_id: str,
                    document_id: str, message: str) -> tuple[str, bool]:
        history = self.store.context_messages(session_id, task_id)
        turns: list[list[dict]] = []
        round_number = 0
        self.store.publish(document_id, "phase", {"task_id": task_id, "phase": "thinking"})
        while True:
            round_number += 1
            response = await agents.respond(task_id, "main", round_number, agents.main_messages(
                task_id, message, self.store.get_document(document_id), history, recent_tool_exchanges(turns),
            ))
            calls = response.get("tool_calls", [])
            if not calls:
                active = [agent for agent in self.store.agents.list_agents(session_id)
                          if agent["owner_task_id"] == task_id and agent["state"] == "Running"]
                pending = self.store.agents.pending_results(session_id, task_id=task_id)
                if active or pending:
                    turns.append([response, {"role": "user", "content": json.dumps({
                        "instruction": "Finish or close active child work, and apply or reject pending results before replying.",
                        "active_agent_ids": [agent["agent_id"] for agent in active],
                        "pending_results": [self.store.agents.result_summary(item) for item in pending],
                    }, ensure_ascii=False)}])
                    continue
                reply = str(response.get("content") or "").strip()
                if not reply:
                    raise ValueError("主 Agent 未返回工具调用或完成说明。")
                runs = self.store.agents.task_runs(session_id, task_id)
                partial = any(run["status"] in {"failed", "paused", "queued", "running"} for run in runs)
                return reply, partial
            exchange = [response]
            for call in calls:
                output = await self.call_main_tool(agents, task_id, session_id, document_id, call, round_number)
                exchange.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(output, ensure_ascii=False)})
            turns.append(exchange)

    async def call_main_tool(self, models: DrawingAgents, task_id: str, session_id: str,
                             document_id: str, call: dict, round_number: int = 0) -> dict:
        caller_id = f"main:{task_id}"
        name, raw_args = call["function"]["name"], call["function"]["arguments"]
        self.store.agents.check_task(session_id, task_id)
        if self.store.session_document_id(session_id) != document_id:
            raise KeyError("Main Agent tool call targets a different canvas")
        self.store.log_tool(task_id, "main", round_number, "function_call", {
            "type": "function_call", "call_id": call["id"], "name": name, "arguments": raw_args,
        })
        output = self.store.agents.begin_call(task_id, caller_id, call["id"], name, raw_args)
        cached = output is not None
        if not cached:
            try:
                args = validate_tool(name, json.loads(raw_args))
                if name == "spawn_agent":
                    output = await self.children.spawn_agent(session_id, task_id, call["id"], args, models.settings)
                elif name == "send_input":
                    output = await self.children.send_input(session_id, task_id, call["id"], args, models.settings)
                elif name == "wait_agent":
                    output = await self.children.wait_agent(session_id, args["ids"], args.get("after"))
                elif name == "close_agent":
                    output = await self.children.close_agent(session_id, args["agent_id"])
                elif name == "resume_agent":
                    output = await self.children.resume_agent(session_id, task_id, args["agent_id"], models.settings)
                elif name == "apply_agent_result":
                    output = await self.apply_result(task_id, session_id, document_id, args["result_id"])
                elif name == "reject_agent_result":
                    output = self.store.agents.reject_result(session_id, args["result_id"], args["reason"])
                elif name in CHILD_TOOL_NAMES:
                    output = await self._direct_tool(task_id, session_id, document_id, call["id"], name, args)
                else:
                    raise ValueError(f"Unknown main Agent tool: {name}")
            except (ValueError, KeyError, TypeError, VersionConflict) as exc:
                output = {"ok": False, "error": validation_summary(exc)}
            self.store.agents.finish_call(caller_id, call["id"], output)
        self.store.log_tool(task_id, "main", round_number, "function_call_output", {
            "type": "function_call_output", "call_id": call["id"],
            "output": json.dumps(output, ensure_ascii=False), "cached": cached,
        })
        return output

    async def _direct_tool(self, task_id: str, session_id: str, document_id: str,
                           call_id: str, name: str, args: dict) -> dict:
        result_id = stable_id("result", f"{task_id}/{call_id}/direct")
        with self.store.lock:
            existing = self.store.connection.execute("SELECT id FROM drawing_results WHERE id=?", (result_id,)).fetchone()
        if existing is not None:
            return await self.apply_result(task_id, session_id, document_id, result_id)
        document = self.store.get_document(document_id)
        _, _, prepared, output = prepare_drawing_tool(name, args, document, document["elements"], document["selection"])
        if prepared:
            result = self.store.agents.create_direct_result(session_id, task_id, call_id, prepared, name)
            output = {**output, **await self.apply_result(task_id, session_id, document_id, result["id"])}
        return output

    async def apply_result(self, task_id: str, session_id: str, document_id: str, result_id: str) -> dict:
        self.store.agents.check_task(session_id, task_id)
        if self.store.session_document_id(session_id) != document_id:
            raise KeyError("Drawing result submission targets a different canvas")
        result = self.store.agents.get_result(session_id, result_id)
        if result["rejected_reason"]:
            raise ValueError("This drawing result has been rejected")
        changed_ids = []
        for index in range(result["committed_count"], len(result["actions"])):
            item = result["actions"][index]
            action = item["action"]
            with self.store.lock:
                _, duplicate = self.store.agents.validate_commit(document_id, result_id, index)
            if duplicate:
                continue
            if action["kind"] == "create_element" and action["args"]["type"] == "freedraw":
                await self._animate_stroke(task_id, document_id, action)
            try:
                outcome = self.store.commit_action(document_id, action, item["expected_elements"], (result_id, index))
            except (ValueError, VersionConflict):
                self.store.publish(document_id, "scene", {"document": self.store.get_document(document_id), "source": "error"})
                raise
            if not outcome.get("already_committed"):
                changed_ids.extend(outcome["changed_ids"])
                self.store.log_tool(task_id, "main", index + 1, "commit", {
                    "result_id": result_id, "kind": action["kind"], "changed_ids": outcome["changed_ids"],
                    "document_version": outcome["document"]["version"],
                })
                self.store.publish(document_id, "phase", {"task_id": task_id, "phase": "drawing",
                                                           "completed": index + 1, "total": len(result["actions"])})
                if len(result["actions"]) > 1:
                    await asyncio.sleep(0.09)
        saved = self.store.agents.get_result(session_id, result_id)
        document = self.store.get_document(document_id)
        return {"ok": True, **self.store.agents.result_summary(saved), "changed_ids": changed_ids,
                "selected_ids": document["selection"], "document_version": document["version"]}

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

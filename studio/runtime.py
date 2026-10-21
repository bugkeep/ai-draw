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
from .agents import DrawingAgents, prepare_drawing_tool, recent_tool_exchanges, model_output_feedback
from .errors import validation_summary
from .fast_path import fast_actions
from .settings import ModelSettings
from .research import DrawingResearch, RESEARCH_TOOLS
from .store import StudioStore, VersionConflict
from .vision import CanvasVision
from .memory import AgentMemory, MEMORY_TOOLS
from .reference_images import IMAGE_QUERY_TOOLS, TRACE_TOOLS, ReferenceImages
from .result_review import AgentResultReview
from .quality import DrawingQuality, review_scene_key
from .completion import ReplyCompletion
from .drawing_dsl import require_current_result
from .drawing_progress import completion_context, completion_progress


class DrawingRuntime:
    def __init__(self, store: StudioStore, settings: ModelSettings):
        self.store = store
        self.settings = settings
        self.running: dict[str, asyncio.Task] = {}
        self.document_locks: dict[str, asyncio.Lock] = {}
        self.cancel_requested: set[str] = set()
        self.vision = CanvasVision(store)
        self.result_reviews = AgentResultReview(store, self.vision)
        self.quality = DrawingQuality(store, self.vision)
        self.reply_completion = ReplyCompletion(store, self.vision, self.quality)
        self.memory = AgentMemory(store)
        self.references = ReferenceImages(store)
        self.children = AgentManager(store, settings, self.vision, self.memory, self.references)

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
        self.store.publish(document_id, "task", {"task_id": task["id"], **task})
        self.running[task["id"]] = asyncio.create_task(
            self._run(task["id"], session_id, document_id, message)
        )
        self.running[task["id"]].add_done_callback(lambda worker: self._task_done(task["id"], worker))
        return task

    def _task_done(self, task_id: str, worker: asyncio.Task) -> None:
        if self.running.get(task_id) is worker:
            self.running.pop(task_id, None)
        self.cancel_requested.discard(task_id)

    def cancel(self, task_id: str) -> None:
        task = self.running.get(task_id)
        if task is None:
            if self.store.get_task(task_id)["status"] in {"completed", "partial", "failed", "cancelled"}:
                return
            raise KeyError(f"Running task not found: {task_id}")
        if self.store.get_task(task_id)["status"] == "queued":
            self.cancel_requested.add(task_id)
            record = self.store.get_task(task_id)
            self._status(task_id, self.store.session_document_id(record["session_id"]), "cancelled", "绘图已停止。",
                         latency_ms=(time.time() - record["created_at"]) * 1000)
        task.cancel()

    async def close(self) -> None:
        for task in self.running.values():
            task.cancel()
        if self.running:
            await asyncio.gather(*self.running.values(), return_exceptions=True)
        await self.children.close()
        await self.vision.close()

    def _status(self, task_id: str, document_id: str, status: str,
                summary: str = "", error: str = "", latency_ms: float = 0) -> None:
        self.store.update_task(task_id, status, summary, error, latency_ms)
        self.store.publish(document_id, "task", {
            **self.store.get_task(task_id),
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
        self.quality.begin(task_id, self.store.get_document(document_id), message)
        turns: list[list[dict]] = []
        round_number = 0
        review_pending, last_tools_failed = False, False
        self.store.publish(document_id, "phase", {"task_id": task_id, "phase": "thinking"})
        while True:
            round_number += 1
            if review_pending and not last_tools_failed and self._child_work_settled(session_id, task_id):
                try:
                    checkpoint = await self.quality.assess(task_id, agents.settings)
                except ValueError as error:
                    checkpoint = {'ok': False, 'error': str(error)}
                # Valid assessments are reattached below from SQLite. Preserve
                # malformed responses and provider errors in full as well.
                if not checkpoint.get('review_key'):
                    turns.append([{'role': 'user', 'content': json.dumps({
                        'quality_checkpoint_error': checkpoint}, ensure_ascii=False)}])
                review_pending = False
            document = self.store.get_document(document_id)
            pictures, observations = await self.vision.context(task_id, 'main', f'main:{task_id}', document)
            references, reference_ids = await self.references.prepare_context(task_id, f'main:{task_id}')
            reviews, review_deliveries = await self.result_reviews.context(task_id)
            quality_context, quality_keys = self.quality.context(task_id, agents.settings, document)
            messages = self.memory.prepare_messages(task_id, 'main', f'main:{task_id}',
                f"{document_id}:{document['version']}", document, agents.main_messages(
                task_id, message, document, history, recent_tool_exchanges(turns),
            )) + references + pictures + reviews + completion_context(self.store, task_id, document, quality_context)
            response = await agents.respond(task_id, "main", round_number, messages, observation_ids=observations,
                                            reference_image_ids=reference_ids,
                                            result_observation_ids=[item for items in review_deliveries.values() for item in items],
                                            quality_review_keys=quality_keys)
            self.vision.delivered(task_id, f'main:{task_id}', observations)
            self.references.delivered(task_id, f'main:{task_id}', reference_ids)
            self.result_reviews.delivered(task_id, review_deliveries)
            self.memory.delivered(f'main:{task_id}', messages)
            calls = response.get("tool_calls", [])
            incomplete = model_output_feedback(self.store, task_id, 'main', response)
            if incomplete:
                self.store.log_tool(task_id, 'main', round_number, 'model_output_truncated', incomplete)
            if not calls:
                if incomplete:
                    turns.append([response, {'role': 'user', 'content': json.dumps(incomplete, ensure_ascii=False)}])
                    continue
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
                try:
                    completion = await self._complete_task(agents, task_id, session_id, document_id, reply)
                except ValueError as error:
                    completion = {'ok': False, 'error': str(error)}
                self.store.log_tool(task_id, 'main', round_number, 'completion_candidate', {
                    'source': 'no_tool_calls', 'summary': reply, 'result': completion})
                if not completion['ok']:
                    turns.append([response, {'role': 'user', 'content': json.dumps(completion, ensure_ascii=False)}])
                    continue
                return completion['summary'], completion['partial']
            exchange = [response]
            errors = False
            compacted = False
            for call in calls:
                output = await self.call_main_tool(agents, task_id, session_id, document_id, call, round_number)
                current = self.store.get_document(document_id)
                context_output = self.memory.for_context(task_id, 'main', f'main:{task_id}', call['id'],
                    call['function']['name'], call['function']['arguments'], output, f"{document_id}:{current['version']}")
                exchange.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(context_output, ensure_ascii=False)})
                errors = errors or output.get("ok") is False
                if call['function']['name'] == 'apply_agent_result' and output.get('ok') and output.get('changed_ids'):
                    review_pending = True
                compacted = compacted or call['function']['name'] == 'compact_context' and output.get('ok') is True
            if compacted:
                turns.clear()
            if incomplete:
                exchange.append({'role': 'user', 'content': json.dumps(incomplete, ensure_ascii=False)})
            turns.append(exchange)
            last_tools_failed = errors

    def _child_work_settled(self, session_id: str, task_id: str) -> bool:
        return (not any(agent['owner_task_id'] == task_id and agent['state'] == 'Running'
                        for agent in self.store.agents.list_agents(session_id))
                and not self.store.agents.pending_results(session_id, task_id=task_id))

    async def _complete_task(self, models, task_id, session_id, document_id, summary):
        if not self._child_work_settled(session_id, task_id):
            raise ValueError('Settle active Agents and pending results before completing')
        progress = completion_progress(self.store, task_id, self.store.get_document(document_id))
        if progress is not None and not progress['ok']:
            return progress
        self.vision.require_review(task_id, f'main:{task_id}', self.store.get_document(document_id))
        review = await self.reply_completion.assess(task_id, models.settings, summary)
        if review is None or review.get('requested_output') == 'canvas':
            review = await self.quality.assess(task_id, models.settings)
        if not review['ok']:
            return review
        if not summary:
            if review.get('requested_output') == 'reply':
                return {'ok': False, 'error': 'Provide the requested conversation reply before completing.'}
            summary = review['assessment']['summary']
        if not self._child_work_settled(session_id, task_id):
            raise ValueError('Child work changed during completion review; settle current work before completing')
        if review_scene_key(self.store.get_document(document_id)) != review['scene_key']:
            raise ValueError('Canvas changed during completion review; inspect the current image before completing')
        progress = completion_progress(self.store, task_id, self.store.get_document(document_id))
        if progress is not None and not progress['ok']:
            return progress
        runs = self.store.agents.task_runs(session_id, task_id)
        return {'ok': True, 'summary': summary,
            **({'quality_review_key': review['review_key']} if 'review_key' in review else
               {'reply_review_key': review['reply_review_key']}),
            'partial': any(run['status'] in {'failed', 'paused', 'queued', 'running'} for run in runs)}

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
            proposed = None
            try:
                proposed = json.loads(raw_args)
                args = validate_tool(name, proposed)
                if name in MEMORY_TOOLS:
                    output = self.memory.call(task_id, 'main', name, args, self.store.get_document(document_id))
                elif name == 'view_canvas':
                    output = await self.vision.observe(task_id, 'main', caller_id,
                                                       self.store.get_document(document_id), args.get('region'), remember_region=True)
                elif name in RESEARCH_TOOLS:
                    output = await DrawingResearch(self.store).call(task_id, "main", name, args)
                elif name in IMAGE_QUERY_TOOLS:
                    output = await self.references.call(task_id, 'main', caller_id, name, args)
                elif name == "spawn_agent":
                    output = await self.children.spawn_agent(session_id, task_id, call["id"], args, models.settings)
                elif name == "send_input":
                    output = await self.children.send_input(session_id, task_id, call["id"], args, models.settings)
                elif name == "wait_agent":
                    output = await self.children.wait_agent(session_id, args["ids"], args.get("after"))
                elif name == "close_agent":
                    output = await self.children.close_agent(session_id, args["agent_id"])
                elif name == "resume_agent":
                    output = await self.children.resume_agent(session_id, task_id, args["agent_id"], models.settings)
                elif name == 'continue_agent':
                    output = await self.children.continue_agent(session_id, task_id, args, models.settings)
                elif name == "apply_agent_result":
                    self.result_reviews.require_review(task_id, args['result_id'])
                    output = await self.apply_result(task_id, session_id, document_id, args["result_id"])
                elif name == 'view_agent_result':
                    output = await self.result_reviews.observe(task_id, args['result_id'], args.get('region'))
                elif name == "reject_agent_result":
                    output = self.store.agents.reject_result(session_id, args["result_id"], args["reason"])
                elif name in CHILD_TOOL_NAMES:
                    output = await self._direct_tool(task_id, session_id, document_id, call["id"], name, args)
                else:
                    raise ValueError(f"Unknown main Agent tool: {name}")
            except (ValueError, KeyError, TypeError, VersionConflict) as exc:
                output = {"ok": False, "error": validation_summary(exc)}
                spec = proposed.get('drawspec') if isinstance(proposed, dict) else None
                draft_id = (call['id'] if isinstance(spec, dict) and isinstance(spec.get('composition'), dict)
                    else spec['amend'].get('source_call_id') if isinstance(spec, dict) and isinstance(spec.get('amend'), dict) else None)
                if (name in {'spawn_agent', 'send_input'} and isinstance(spec, dict)
                        and isinstance(draft_id, str) and draft_id):
                    output['drawspec_draft_call_id'] = draft_id
                    output['repair_instruction'] = ('Keep stage_id/intent/success_criteria and use drawspec.amend.source_call_id '
                        'with this ID. Send only corrected complete records by ID, or the corrected full frame/art_direction/perspective. '
                        'Retain all corrections relative to that original failed full call; earlier unsuccessful amendments are not published. '
                        'Unchanged records are reused from SQLite and the complete plan is validated before dispatch.')
            self.store.agents.finish_call(caller_id, call["id"], output)
        current = self.store.get_document(document_id)
        self.memory.register(task_id, 'main', caller_id, call['id'], name, raw_args, output,
                             f"{document_id}:{current['version']}")
        self.store.log_tool(task_id, "main", round_number, "function_call_output", {
            "type": "function_call_output", "call_id": call["id"],
            "output": json.dumps(output, ensure_ascii=False), "cached": cached,
        })
        return output

    async def _direct_tool(self, task_id: str, session_id: str, document_id: str,
                           call_id: str, name: str, args: dict) -> dict:
        result_id = stable_id("result", f"{task_id}/{call_id}/direct")
        trace = {}
        preparation_name, preparation_args = name, args
        if name in TRACE_TOOLS:
            prepare = self.references.trace if name == 'trace_reference' else self.references.continue_trace
            items, trace = await prepare(task_id, f'main:{task_id}', args)
            preparation_name, preparation_args = 'create_elements', {'items': items}
        with self.store.lock:
            existing = self.store.connection.execute("SELECT id FROM drawing_results WHERE id=?", (result_id,)).fetchone()
        if existing is not None:
            return {**trace, **await self.apply_result(task_id, session_id, document_id, result_id)}
        document = self.store.get_document(document_id)
        if name == 'update_elements' and 'points' in args.get('patch', {}):
            targets = set(args['ids'])
            if any(element['id'] in targets and element['type'] == 'freedraw' for element in document['elements']):
                raise ValueError('Fine pen geometry belongs to a drawing child Agent. Use spawn_agent or send_input '
                                 'with these object IDs for curve changes; direct moves and style updates remain available.')
        preparation_args, reference_coordinates = self.references.ground_tool(
            task_id, f'main:{task_id}', document, preparation_name, preparation_args)
        preparation_args, coordinates = self.vision.ground_tool(
            task_id, f'main:{task_id}', document, preparation_name, preparation_args)
        _, _, prepared, output = prepare_drawing_tool(preparation_name, preparation_args, document, document["elements"], document["selection"])
        output.update(trace)
        output.update(coordinates)
        output.update(reference_coordinates)
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
        require_current_result(self.store, task_id, result)
        changed_ids = []
        for index in range(result["committed_count"], len(result["actions"])):
            item = result["actions"][index]
            action = item["action"]
            with self.store.lock:
                _, duplicate = self.store.agents.validate_commit(document_id, result_id, index)
            if duplicate:
                continue
            pen_stroke = action["kind"] == "create_element" and action["args"]["type"] == "freedraw"
            if pen_stroke:
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
                if len(result["actions"]) > 1 and not pen_stroke:
                    await asyncio.sleep(0.035)
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
            pen_stroke = action["kind"] == "create_element" and action["args"]["type"] == "freedraw"
            if pen_stroke:
                await self._animate_stroke(task_id, document_id, action)
            outcome = self.store.commit_action(document_id, action, expected)
            self.store.log_tool(task_id, "main", index, "commit", {
                "kind": action["kind"], "changed_ids": outcome["changed_ids"],
                "document_version": outcome["document"]["version"],
            })
            self.store.publish(document_id, "phase", {"task_id": task_id, "phase": "drawing",
                                                       "completed": index, "total": total})
            if total > 1 and not pen_stroke:
                await asyncio.sleep(0.035)

    async def _animate_stroke(self, task_id: str, document_id: str, action: dict[str, Any]) -> None:
        element = create_native_element(action["args"], action.get("element_id"))
        points = element["points"]
        length = sum(math.dist(first, second) for first, second in zip(points, points[1:]))
        duration = max(0.032, min(0.32, length / 1200))
        steps = min(len(points) - 1, max(2, math.ceil(duration * 60)))
        for frame in range(1, steps + 1):
            count = max(2, math.ceil(len(points) * frame / steps))
            partial = {**element, "points": points[:count], "pressures": element["pressures"][:count],
                       "width": max(point[0] for point in points[:count]),
                       "height": max(point[1] for point in points[:count])}
            self.store.publish(document_id, "stroke_progress", {"task_id": task_id, "element": partial})
            await asyncio.sleep(duration / steps)

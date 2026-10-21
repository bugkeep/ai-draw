import asyncio
import copy
import json

from .actions import apply_action, matches_fingerprint
from .agent_tools import validate_tool
from .agents import DrawingAgents, prepare_drawing_tool, recent_tool_messages, validate_child_tool, model_output_feedback
from .errors import validation_summary
from .settings import ModelConfig, ModelSettings
from .research import DrawingResearch, RESEARCH_TOOLS
from .store import StudioStore, VersionConflict
from .memory import MEMORY_TOOLS
from .reference_images import IMAGE_QUERY_TOOLS, TRACE_TOOLS
from .draft_review import DraftReview
from .reference_work import ReferenceWork
from .drawing_dsl import prepare_assignment, parse_report, result_evidence
from .child_harness import repair_context, coordinate_context, observation_context
from .child_drawing_memory import drawing_memory_context
from .child_execution import checkpoint_reason, continue_repair, current_feedback, execution_context, recover_repeated_tool_error, verification_checkpoint


class AgentManager:
    def __init__(self, store: StudioStore, settings: ModelSettings, vision, memory, references):
        self.store = store
        self.records = store.agents
        self.settings = settings
        self.vision = vision
        self.memory = memory
        self.references = references
        self.draft_reviews = DraftReview(store, vision)
        self.workers: dict[str, asyncio.Task] = {}
        self.controls: dict[str, asyncio.Lock] = {}
        self.changed = asyncio.Condition()
        self.records.recover_interrupted()

    @property
    def busy(self) -> bool:
        return any(not worker.done() for worker in self.workers.values())

    def session_busy(self, session_id: str) -> bool:
        return any(agent["agent_id"] in self.workers and not self.workers[agent["agent_id"]].done()
                   for agent in self.records.list_agents(session_id))

    def _start(self, session_id: str, task_id: str, agent_id: str, config: ModelConfig) -> None:
        worker = self.workers.get(agent_id)
        if worker is None or worker.done():
            self.workers[agent_id] = asyncio.create_task(self._work(session_id, task_id, agent_id, config))

    async def _notify(self, session_id: str, agent_id: str, result: dict | None = None) -> None:
        agent = self.records.get_agent(session_id, agent_id)
        document_id = self.store.session_document_id(session_id)
        self.store.publish(document_id, "phase", {
            "task_id": agent["owner_task_id"], "phase": "agent_state",
            "agent_id": agent_id, "nickname": agent["nickname"], "state": agent["state"],
            "queued_inputs": len(agent["inputs"]), "result": result, 'continuations': agent['continuations'],
        })
        async with self.changed:
            self.changed.notify_all()

    async def spawn_agent(self, session_id: str, task_id: str, call_id: str,
                           args: dict, config: ModelConfig) -> dict:
        message = prepare_assignment(self.store, session_id, task_id, args['message'], args.get('drawspec'))
        ReferenceWork.load(self.references, task_id, message)
        nickname = args.get("nickname") or f"Artist-{call_id[-6:]}"
        agent = self.records.create_agent(session_id, task_id, call_id, message, nickname,
                                          args.get("relevant_ids", []))
        if agent["state"] == "Running":
            self._start(session_id, task_id, agent["agent_id"], config)
        await self._notify(session_id, agent["agent_id"])
        return agent

    async def send_input(self, session_id: str, task_id: str, call_id: str,
                         args: dict, config: ModelConfig) -> dict:
        message = prepare_assignment(self.store, session_id, task_id, args['message'], args.get('drawspec'),
            agent_id=args['agent_id'])
        ReferenceWork.load(self.references, task_id, message)
        agent_id = args["agent_id"]
        async with self.controls.setdefault(agent_id, asyncio.Lock()):
            agent = self.records.enqueue_input(session_id, task_id, agent_id, call_id,
                                               message, args.get("relevant_ids", []))
            if agent["state"] == "Running":
                self._start(session_id, task_id, agent_id, config)
        await self._notify(session_id, agent_id)
        return agent

    async def close_agent(self, session_id: str, agent_id: str) -> dict:
        async with self.controls.setdefault(agent_id, asyncio.Lock()):
            agent = self.records.pause_agent(session_id, agent_id)
            worker = self.workers.get(agent_id)
            if worker is not None and not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        await self._notify(session_id, agent_id)
        return agent

    async def resume_agent(self, session_id: str, task_id: str, agent_id: str,
                           config: ModelConfig) -> dict:
        async with self.controls.setdefault(agent_id, asyncio.Lock()):
            agent = self.records.resume_agent(session_id, task_id, agent_id)
            if agent["state"] == "Running":
                self._start(session_id, task_id, agent_id, config)
        await self._notify(session_id, agent_id)
        return agent

    async def continue_agent(self, session_id: str, task_id: str, args: dict,
                             config: ModelConfig) -> dict:
        agent_id = args['agent_id']
        async with self.controls.setdefault(agent_id, asyncio.Lock()):
            output = self.records.continue_agent(session_id, task_id, agent_id,
                args['continuation_id'], args.get('message', ''))
            agent = self.records.get_agent(session_id, agent_id)
            if not output['already_continued'] and agent['state'] == 'Running':
                self._start(session_id, task_id, agent_id, config)
        self.store.log_tool(task_id, agent_id, 0, 'agent_continued', output)
        await self._notify(session_id, agent_id)
        return output

    def observe(self, session_id: str, ids: list[str], after: dict[str, int]) -> dict:
        agents, results = [], []
        for agent_id in dict.fromkeys(ids):
            agent = self.records.get_agent(session_id, agent_id)
            agents.append(agent)
            for result in self.records.observed_results(session_id, agent_id, after.get(agent_id, 0)):
                if result["result_id"]:
                    result.update(self.records.result_summary(self.records.get_result(session_id, result["result_id"])))
                results.append(result)
        return {"agents": agents, "results": results,
                'continuations': [item for agent in agents for item in agent['continuations']],
                "running_ids": [agent["agent_id"] for agent in agents if agent["state"] == "Running"],
                "revisions": {agent["agent_id"]: agent["revision"] for agent in agents}}

    async def wait_agent(self, session_id: str, ids: list[str], after: dict[str, int] | None = None) -> dict:
        if not ids:
            raise ValueError("wait_agent needs at least one Agent ID")
        after = after or {}
        if set(after) - set(ids) or any(not isinstance(value, int) or value < 0 for value in after.values()):
            raise ValueError("after must contain non-negative revisions for the requested Agents")
        async with self.changed:
            while True:
                observation = self.observe(session_id, ids, after)
                if observation["results"] or observation['continuations'] or any(agent["state"] in {"Final", "Shutdown"}
                                                 for agent in observation["agents"]):
                    return observation
                await self.changed.wait()

    async def _await_continuation(self, session_id: str, run: dict) -> bool:
        async with self.changed:
            while True:
                agent = self.records.get_agent(session_id, run['agent_id'])
                if agent['state'] == 'Shutdown':
                    return False
                if not any(item['run_id'] == run['id'] for item in agent['continuations']):
                    return True
                await self.changed.wait()

    async def _suspend_truncated_response(self, session_id: str, run: dict,
                                          history: list[dict], feedback: dict, round_number: int) -> None:
        continuation = self.records.suspend_for_continuation(session_id, run['id'], history, feedback)
        self.store.log_tool(run['owner_task_id'], run['agent_id'], round_number,
            'agent_continuation_required', continuation)
        await self._notify(session_id, run['agent_id'])

    async def close_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        for agent in self.records.list_agents(task["session_id"]):
            if agent["owner_task_id"] == task_id and agent["state"] == "Running":
                await self.close_agent(task["session_id"], agent["agent_id"])

    async def close(self) -> None:
        for session in self.store.list_sessions():
            for agent in self.records.list_agents(session["id"]):
                if agent["state"] == "Running":
                    await self.close_agent(session["id"], agent["agent_id"])

    def _initialize(self, session_id: str, run: dict) -> dict:
        if run["document"] is not None:
            self.draft_reviews.begin(run)
            return run
        document = self.store.get_document(self.store.session_document_id(session_id))
        draft, selection = copy.deepcopy(document["elements"]), list(document["selection"])
        dependencies = self.records.pending_results(session_id, agent_id=run["agent_id"])
        for result in dependencies:
            for item in result["actions"][result["committed_count"]:]:
                by_id = {element["id"]: element for element in draft}
                for element_id, expected in item["expected_elements"].items():
                    if element_id not in by_id or not matches_fingerprint(by_id[element_id], expected["fingerprint"]):
                        raise VersionConflict("A previous unsubmitted result conflicts with the current canvas")
                draft, selection, _ = apply_action(draft, selection, item["action"])
        document["depends_on"] = [result["id"] for result in dependencies]
        self.records.initialize_run(run["id"], document, draft, selection)
        run = self.records.get_run(session_id, run['id'])
        self.draft_reviews.begin(run)
        return run

    async def _completion(self, models, run, summary):
        review = await self.draft_reviews.review(run, models.settings, {'completion_candidate': True})
        assessment = review.get('assessment') or {}
        replan = self.draft_reviews.replan_completion(run, review)
        return {'summary': summary or assessment.get('visible_state') or review.get('error') or '未准备绘图修改。',
            'completion': {
            'status': 'ready' if review['ok'] else 'blocked',
            'unresolved': [finding['defect'] for finding in assessment.get('findings', [])]
                or ([review['error']] if review.get('error') else []),
            'review_key': review.get('review_key'), 'review_status': review['status'],
            'visible_state': assessment.get('visible_state', review.get('error', '')),
            'findings': assessment.get('findings', []),
            'next_action': assessment.get('next_action', ''),
            **(replan or {}),
            **({'review_error': review} if review['status'] == 'error' else {}),
            'source': 'no_tool_calls'}}

    def _review_history(self, run, review, history):
        if (not review or review['status'] == 'error'
                or self.draft_reviews.observed(run['id'], review.get('review_key'))):
            return history
        history = self.memory.fold_reads(run['id'], history, checkpoint=True)
        return self.memory.repair_checkpoint(run['id'], history) if review['status'] == 'repair' else history

    async def _work(self, session_id: str, task_id: str, agent_id: str, config: ModelConfig) -> None:
        try:
            while True:
                task_id = self.records.get_agent(session_id, agent_id)["owner_task_id"]
                run = self.records.next_run(session_id, agent_id, task_id)
                if run is None:
                    return
                await self._notify(session_id, agent_id)
                try:
                    models = DrawingAgents(self.store, config)
                    run = self._initialize(session_id, run)
                    await self._execute_run(models, session_id, run)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.store.log_tool(task_id, agent_id, 0, "agent_error", {
                        "run_id": run["id"], "type": type(exc).__name__, "message": str(exc),
                    })
                    result = self.records.complete_run(session_id, run["id"], "", validation_summary(exc))
                    self.store.log_tool(task_id, agent_id, 0, "agent_result", result)
                    await self._notify(session_id, agent_id, result)
        except asyncio.CancelledError:
            self.records.pause_agent(session_id, agent_id)
            raise
        finally:
            if self.workers.get(agent_id) is asyncio.current_task():
                self.workers.pop(agent_id, None)
            async with self.changed:
                self.changed.notify_all()

    def _initial_target_blocker(self, run):
        targets = set(run['relevant_ids'])
        if (not targets or run['prepared'] or run['conversation'] or run['pending_response'] is not None
                or targets & {item['id'] for item in run['draft'] if not item.get('isDeleted')}):
            return None
        with self.store.lock:
            started = self.store.connection.execute(
                'SELECT 1 FROM agent_tool_calls WHERE caller_id=? LIMIT 1', (run['id'],)).fetchone()
        if started:
            return None
        return {'status': 'blocked', 'reason': 'missing_edit_targets', 'stopped_by': 'runtime_input_validation',
                'unresolved': ['None of the assigned edit targets exists in this input\'s starting private draft. '
                    'Use current object IDs, retain the pending candidate when repairing it, or explicitly plan new work.'],
                'missing_ids': sorted(targets),
                'available_object_count': sum(not item.get('isDeleted') for item in run['draft'])}

    async def _execute_run(self, models: DrawingAgents, session_id: str, run: dict) -> None:
        blocker = self._initial_target_blocker(run)
        if blocker:
            summary = 'Assigned edit targets are absent; no drawing or model request was started.'
            result = self.records.complete_run(session_id, run['id'], summary, completion=blocker)
            self.store.log_tool(run['owner_task_id'], run['agent_id'], 0, 'agent_input_blocked', {
                'run_id': run['id'], 'completion': blocker})
            self.store.log_tool(run['owner_task_id'], run['agent_id'], 0, 'agent_result', result)
            await self._notify(session_id, run['agent_id'], result)
            return
        source_work = ReferenceWork.load(self.references, run['owner_task_id'], run['message'])
        round_number = 0
        while True:
            if not await self._await_continuation(session_id, run):
                return
            round_number += 1
            run = self.records.get_run(session_id, run["id"])
            message = run["pending_response"]
            history = list(recent_tool_messages(run['conversation']) if round_number == 1 else run["conversation"])
            if message is not None:
                for index in range(len(history) - 1, -1, -1):
                    if history[index]["role"] == "assistant":
                        if history[index] == message:
                            history = history[:index]
                        break
            if message is None:
                history = recover_repeated_tool_error(self.memory, run, history)
            run["conversation"] = history
            if message is None:
                agent = self.records.get_agent(session_id, run["agent_id"])
                document = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
                pictures, observations = await self.vision.context(
                    run['owner_task_id'], run['agent_id'], run['id'], document)
                if source_work:
                    source_work.prepare_source(run['id'])
                references, reference_ids = await self.references.prepare_context(run['owner_task_id'], run['id'])
                repeated = self.memory.repeated_read(run['id'], f"{run['id']}:{len(run['prepared'])}")
                trigger = {'repeated_read': repeated} if repeated else self.draft_reviews.follow_up(run)
                reason = checkpoint_reason(trigger) or verification_checkpoint(self.store, run, trigger)
                review = (await self.draft_reviews.review(run, models.settings, reason)
                          if reason else current_feedback(self.store, run))
                replan = self.draft_reviews.replan_completion(run, review) if review else None
                if review and review['status'] != 'error':
                    history = self._review_history(run, review, history)
                    if review['status'] == 'repair':
                        await self.draft_reviews.focus(run, review)
                        pictures, observations = await self.vision.context(
                            run['owner_task_id'], run['agent_id'], run['id'], document)
                    run['conversation'] = history
                messages = observation_context(self.memory.prepare_messages(run['owner_task_id'], run['agent_id'], run['id'],
                    f"{run['id']}:{len(run['prepared'])}", document, models.child_messages(run, agent,
                        source_work.context() if source_work else None)), references, pictures)
                if review:
                    messages.append(repair_context(self.draft_reviews, review, document, replan))
                guide = coordinate_context(self.store, run, observations)
                if guide:
                    messages.append(guide)
                messages.append(drawing_memory_context(self.memory, run, messages))
                messages = execution_context(run, messages)
                message = await models.respond(run["owner_task_id"], run["agent_id"], round_number,
                                               messages, child=True,
                                               observation_ids=observations, reference_image_ids=reference_ids,
                                               draft_review_keys=[review['review_key']] if review and review.get('review_key') else [],
                                               draft_handoff_review_keys=[],
                                               drawspec_coordinate_observation_ids=observations if guide else [])
                self.vision.delivered(run['owner_task_id'], run['id'], observations)
                self.references.delivered(run['owner_task_id'], run['id'], reference_ids)
                self.memory.delivered(run['id'], messages)
                if review:
                    self.draft_reviews.delivered(run['id'], review.get('review_key'))
                self.records.save_response(run, message)
            calls = message.get("tool_calls", [])
            incomplete = model_output_feedback(self.store, run['owner_task_id'], run['agent_id'], message, run['id'])
            if incomplete:
                self.store.log_tool(run['owner_task_id'], run['agent_id'], round_number, 'model_output_truncated', incomplete)
            if not calls:
                if incomplete:
                    await self._suspend_truncated_response(session_id, run, [*history, message,
                        {'role': 'user', 'content': json.dumps(incomplete, ensure_ascii=False)}], incomplete, round_number)
                    continue
                summary = str(message.get("content") or "").strip()
                try:
                    summary, drawing_report = parse_report(summary, run)
                except ValueError as exc:
                    error = {'ok': False, 'error_kind': 'drawing_protocol', 'error': validation_summary(exc),
                        'instruction': 'The input is unfinished. Continue drawing if needed; when returning without tools, '
                            'provide the DrawSpec report format in your current input. Keep tool errors and unresolved work explicit.'}
                    self.store.log_tool(run['owner_task_id'], run['agent_id'], round_number, 'drawing_protocol_error', error)
                    self.records.finish_response(run['id'], [*history, message,
                        {'role': 'user', 'content': json.dumps(error, ensure_ascii=False)}])
                    continue
                if run['prepared']:
                    self.vision.require_review(run['owner_task_id'], run['id'], {
                        **run['document'], 'elements': run['draft'], 'selection': run['selection']})
                completion = await self._completion(models, run, summary)
                feedback_pending = not self.draft_reviews.observed(run['id'], completion['completion'].get('review_key'))
                if continue_repair(completion, drawing_report, feedback_pending=feedback_pending):
                    feedback = {'drawing_checkpoint': {
                        'status': 'repair', 'review_key': completion['completion']['review_key'],
                        'instruction': 'The independent reviewer found repairable work in this same input. '
                            'Continue from the current native draft and saved step. The next request contains '
                            'the full review and refreshed images. Repair the specified structure with a changed '
                            'method when necessary; preserve completed forms. Return a new report when this '
                            'assigned pass is ready for review. No actions have been submitted or repeated.',
                    }}
                    self.store.log_tool(run['owner_task_id'], run['agent_id'], round_number,
                        'drawing_repair_continued', {'run_id': run['id'], **feedback})
                    self.records.finish_response(run['id'], [*history, message,
                        {'role': 'user', 'content': json.dumps(feedback, ensure_ascii=False)}])
                    continue
                if drawing_report is not None:
                    report_unresolved = drawing_report['unresolved'] + [item['evidence'] for item in drawing_report['checks']
                        if item['status'] == 'unresolved']
                    completion['completion']['drawspec'] = result_evidence(self.store, run, drawing_report,
                        completion['completion'].get('review_key'))
                    if report_unresolved:
                        completion['completion']['status'] = 'blocked'
                        completion['completion']['unresolved'] = list(dict.fromkeys([
                            *completion['completion']['unresolved'], *report_unresolved]))
                self.store.log_tool(run['owner_task_id'], run['agent_id'], round_number,
                    'completion_candidate', {'run_id': run['id'], 'source': 'no_tool_calls', 'result': completion})
                result = self.records.complete_run(session_id, run["id"], completion['summary'], completion=completion['completion'])
                self.store.log_tool(run["owner_task_id"], run["agent_id"], round_number, "agent_result", result)
                await self._notify(session_id, run["agent_id"], result)
                return
            exchange = [message]
            compacted = False
            for call in calls:
                name, raw_args = call["function"]["name"], call["function"]["arguments"]
                self.store.log_tool(run["owner_task_id"], run["agent_id"], round_number, "function_call", {
                    "type": "function_call", "call_id": call["id"], "name": name,
                    "arguments": raw_args, "run_id": run["id"], "input_id": run["input_id"],
                })
                output = self.records.begin_call(run["owner_task_id"], run["id"], call["id"], name, raw_args)
                cached = output is not None
                if not cached:
                    try:
                        validate_child_tool(name)
                        args = json.loads(raw_args)
                        if name in MEMORY_TOOLS:
                            args = validate_tool(name, args, child=True)
                            output = self.memory.call(run['owner_task_id'], run['agent_id'], name, args, {
                                **run['document'], 'elements': run['draft'], 'selection': run['selection']})
                        elif name == 'view_canvas':
                            args = validate_tool(name, args, child=True)
                            output = await self.vision.observe(run['owner_task_id'], run['agent_id'], run['id'], {
                                **run['document'], 'elements': run['draft'], 'selection': run['selection']},
                                args.get('region'), remember_region=True)
                        elif name in RESEARCH_TOOLS:
                            args = validate_tool(name, args, child=True)
                            output = await DrawingResearch(self.store).call(run["owner_task_id"], run["agent_id"], name, args)
                        elif name in IMAGE_QUERY_TOOLS:
                            args = validate_tool(name, args, child=True)
                            output = await self.references.call(run['owner_task_id'], run['agent_id'], run['id'], name, args)
                        else:
                            trace = {}
                            preparation_name = name
                            if name in TRACE_TOOLS:
                                args = validate_tool(name, args, child=True)
                                prepare = self.references.trace if name == 'trace_reference' else self.references.continue_trace
                                items, trace = await prepare(run['owner_task_id'], run['id'], args)
                                preparation_name, args = 'create_elements', {'items': items}
                            else:
                                args = validate_tool(name, args, child=True)
                            current = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
                            args, reference_coordinates = self.references.ground_tool(
                                run['owner_task_id'], run['id'], current, preparation_name, args)
                            args, coordinates = self.vision.ground_tool(run['owner_task_id'], run['id'], {
                                **run['document'], 'elements': run['draft'], 'selection': run['selection']},
                                preparation_name, args)
                            draft, selection, actions, output = prepare_drawing_tool(
                                preparation_name, args, run["document"], run["draft"], run["selection"],
                            )
                            output.update(trace)
                            output.update(coordinates)
                            output.update(reference_coordinates)
                            run["draft"], run["selection"] = draft, selection
                            run["prepared"].extend(actions)
                    except (ValueError, KeyError, TypeError) as exc:
                        output = {"ok": False, "error": validation_summary(exc)}
                compacted = compacted or name == 'compact_context' and output.get('ok') is True
                if not cached:
                    self.records.finish_call(run["id"], call["id"], output, run)
                context_output = self.memory.for_context(run['owner_task_id'], run['agent_id'], run['id'],
                    call['id'], name, raw_args, output, f"{run['id']}:{len(run['prepared'])}")
                exchange.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(context_output, ensure_ascii=False)})
                self.store.log_tool(run["owner_task_id"], run["agent_id"], round_number, "function_call_output", {
                    "type": "function_call_output", "call_id": call["id"],
                    "output": json.dumps(output, ensure_ascii=False), "run_id": run["id"], "cached": cached,
                })
                await asyncio.sleep(0)
            if incomplete:
                exchange.append({'role': 'user', 'content': json.dumps(incomplete, ensure_ascii=False)})
            conversation = recent_tool_messages(([] if compacted else history) + exchange)
            if incomplete:
                await self._suspend_truncated_response(session_id, run, conversation, incomplete, round_number)
            else:
                self.records.finish_response(run["id"], conversation)

import asyncio
import copy
import json

from .actions import apply_action, matches_fingerprint
from .agent_tools import validate_tool
from .agents import DrawingAgents, prepare_drawing_tool, recent_tool_messages, validate_child_tool
from .errors import AgentOutputError, validation_summary
from .settings import ModelConfig, ModelSettings
from .research import DrawingResearch, RESEARCH_TOOLS
from .store import StudioStore, VersionConflict


class AgentManager:
    def __init__(self, store: StudioStore, settings: ModelSettings):
        self.store = store
        self.records = store.agents
        self.settings = settings
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
            "queued_inputs": len(agent["inputs"]), "result": result,
        })
        async with self.changed:
            self.changed.notify_all()

    async def spawn_agent(self, session_id: str, task_id: str, call_id: str,
                           args: dict, config: ModelConfig) -> dict:
        nickname = args.get("nickname") or f"Artist-{call_id[-6:]}"
        agent = self.records.create_agent(session_id, task_id, call_id, args["message"], nickname,
                                          args.get("relevant_ids", []))
        if agent["state"] == "Running":
            self._start(session_id, task_id, agent["agent_id"], config)
        await self._notify(session_id, agent["agent_id"])
        return agent

    async def send_input(self, session_id: str, task_id: str, call_id: str,
                         args: dict, config: ModelConfig) -> dict:
        agent_id = args["agent_id"]
        async with self.controls.setdefault(agent_id, asyncio.Lock()):
            agent = self.records.enqueue_input(session_id, task_id, agent_id, call_id,
                                               args["message"], args.get("relevant_ids", []))
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
                if observation["results"] or any(agent["state"] in {"Final", "Shutdown"}
                                                 for agent in observation["agents"]):
                    return observation
                await self.changed.wait()

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
        return self.records.get_run(session_id, run["id"])

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

    async def _execute_run(self, models: DrawingAgents, session_id: str, run: dict) -> None:
        round_number = 0
        restored_history = recent_tool_messages(self.records.recent_messages(run["id"]))
        while True:
            round_number += 1
            run = self.records.get_run(session_id, run["id"])
            message = run["pending_response"]
            history = list(restored_history if round_number == 1 else run["conversation"])
            if message is not None:
                for index in range(len(history) - 1, -1, -1):
                    if history[index]["role"] == "assistant":
                        if history[index] == message:
                            history = history[:index]
                        break
            run["conversation"] = history
            if message is None:
                agent = self.records.get_agent(session_id, run["agent_id"])
                message = await models.respond(run["owner_task_id"], run["agent_id"], round_number,
                                               models.child_messages(run, agent), child=True)
                self.records.save_response(run, message)
            calls = message.get("tool_calls", [])
            if not calls:
                summary = str(message.get("content") or "").strip()
                if not summary:
                    raise AgentOutputError("子 Agent 未返回工具调用或完成说明。")
                result = self.records.complete_run(session_id, run["id"], summary)
                self.store.log_tool(run["owner_task_id"], run["agent_id"], round_number, "agent_result", result)
                await self._notify(session_id, run["agent_id"], result)
                return
            exchange = [message]
            errors = False
            completed_summary = None
            for index, call in enumerate(calls):
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
                        if name == "finish_drawing":
                            args = validate_tool(name, args, child=True)
                            if errors or index != len(calls) - 1:
                                raise ValueError("Read all tool errors before completing; finish_drawing must be last.")
                            output = {"ok": True, "summary": args["summary"]}
                        elif name in RESEARCH_TOOLS:
                            args = validate_tool(name, args, child=True)
                            output = await DrawingResearch(self.store).call(run["owner_task_id"], run["agent_id"], name, args)
                        else:
                            draft, selection, actions, output = prepare_drawing_tool(
                                name, args, run["document"], run["draft"], run["selection"],
                            )
                            run["draft"], run["selection"] = draft, selection
                            run["prepared"].extend(actions)
                    except (ValueError, KeyError, TypeError) as exc:
                        output = {"ok": False, "error": validation_summary(exc)}
                errors = errors or output.get("ok") is False
                if name == "finish_drawing" and output.get("ok"):
                    completed_summary = output["summary"]
                exchange.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(output, ensure_ascii=False)})
                if not cached:
                    self.records.finish_call(run["id"], call["id"], output, run)
                self.store.log_tool(run["owner_task_id"], run["agent_id"], round_number, "function_call_output", {
                    "type": "function_call_output", "call_id": call["id"],
                    "output": json.dumps(output, ensure_ascii=False), "run_id": run["id"], "cached": cached,
                })
                await asyncio.sleep(0)
            self.records.finish_response(run["id"], recent_tool_messages(history + exchange))
            if completed_summary is not None and not errors:
                result = self.records.complete_run(session_id, run["id"], completed_summary)
                self.store.log_tool(run["owner_task_id"], run["agent_id"], round_number, "agent_result", result)
                await self._notify(session_id, run["agent_id"], result)
                return

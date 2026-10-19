import copy
import json
import time
from typing import Any

from .actions import apply_action, element_fingerprint, validate_action
from .agent_tools import CHILD_TOOL_NAMES, DRAWING_TOOLS, MAIN_TOOLS, validate_tool
from .llm import ModelGateway
from .settings import ModelConfig
from .store import StudioStore


TOOL_DEFINITIONS = DRAWING_TOOLS


def recent_tool_exchanges(turns: list[list[dict]], character_budget: int = 48_000) -> list[dict]:
    selected, size = [], 0
    for turn in reversed(turns):
        turn_size = len(json.dumps(turn, ensure_ascii=False))
        if selected and size + turn_size > character_budget:
            break
        selected.append(turn)
        size += turn_size
    del turns[:len(turns) - len(selected)]
    return [message for turn in reversed(selected) for message in turn]


def recent_tool_messages(messages: list[dict]) -> list[dict]:
    turns: list[list[dict]] = []
    for message in messages:
        if message["role"] == "assistant" or not turns:
            turns.append([])
        turns[-1].append(message)
    return recent_tool_exchanges(turns)


def validate_child_tool(name: str) -> None:
    if name not in CHILD_TOOL_NAMES:
        raise ValueError("Child agents can only use drawing tools and cannot create or delegate to other agents")


def scene_context(document: dict[str, Any], ids: list[str] | None = None, query: str = "") -> dict[str, Any]:
    elements = [element for element in document["elements"] if not element.get("isDeleted")]
    if ids:
        by_id = {element["id"]: element for element in elements}
        if set(ids) - set(by_id):
            raise ValueError("Requested scene objects do not exist")
        chosen = [by_id[element_id] for element_id in ids]
    elif query:
        chosen = [element for element in elements if query.casefold() in
                  f"{element.get('customData', {}).get('name', '')} {element.get('text', '')}".casefold()]
    else:
        chosen = elements[-70:]
        for element in elements:
            if element["id"] in document["selection"] and element not in chosen:
                chosen.insert(0, element)
    return {
        "document_id": document["id"], "version": document["version"],
        "element_count": len(elements), "selected_ids": document["selection"],
        "elements": [
            {"id": element["id"], "name": element.get("customData", {}).get("name", ""),
             "type": element["type"], "x": element["x"], "y": element["y"],
             "width": element.get("width"), "height": element.get("height"),
             "strokeColor": element.get("strokeColor"), "backgroundColor": element.get("backgroundColor"),
             "text": str(element.get("text", ""))[:120], "version": element.get("version", 1)}
            for element in chosen
        ],
    }


def prepare_creation_batch(items: list[dict], draft: list[dict], selection: list[str]) -> tuple:
    if not isinstance(items, list) or not items:
        raise ValueError("create_elements needs a non-empty items array")
    validated = [validate_action({"kind": "create_element", "args": item}) for item in items]
    batch, created_ids = [], []
    for action in validated:
        draft, selection, changed_ids = apply_action(draft, selection, action)
        action["element_id"] = changed_ids[0]
        batch.append({"action": action, "expected_elements": {}})
        created_ids.extend(changed_ids)
    return draft, selection, batch, created_ids


def prepare_drawing_tool(name: str, arguments: dict, document: dict,
                         draft: list[dict], selection: list[str]) -> tuple:
    args = validate_tool(name, arguments, child=True)
    if name == "inspect_scene":
        return draft, selection, [], {"ok": True, "scene": scene_context(
            {**document, "elements": draft, "selection": selection}, args.get("ids"), args.get("query", ""))}
    if name == "create_elements":
        draft, selection, actions, changed_ids = prepare_creation_batch(args["items"], draft, selection)
    else:
        action = validate_action({"kind": name, "args": args})
        by_id = {element["id"]: element for element in draft}
        expected = {element_id: {"version": int(by_id[element_id].get("version", 1)),
                                 "fingerprint": element_fingerprint(by_id[element_id])}
                    for element_id in args.get("ids", []) if element_id in by_id}
        draft, selection, changed_ids = apply_action(draft, selection, action)
        if name == "create_element":
            action["element_id"] = changed_ids[0]
        actions = [{"action": action, "expected_elements": expected}]
    return draft, selection, actions, {"ok": True, "changed_ids": changed_ids,
                                       "selected_ids": selection, "action_count": len(actions)}


MAIN_SYSTEM = (
    "You are the main Agent of a conversational Excalidraw drawing editor. Use the provided native tools. "
    "You control the child Agent lifecycle through spawn_agent, send_input, wait_agent, close_agent and resume_agent. "
    "spawn_agent starts immediately and returns stable agent_id, nickname, input_id and run_id. "
    "For independent parts of a composition, spawn the children together in the same response so they run concurrently. "
    "Give each child a self-contained goal with shared layout bounds, required labels and connector coordinates. "
    "Reuse a suitable existing Agent with send_input for follow-up edits. Running inputs queue sequentially. "
    "Use wait_agent to observe results. It never executes work. For subsequent waits use only returned running_ids, "
    "with their revisions in after. Final and Shutdown Agents always return immediately, even with after. "
    "Review result summaries and call apply_agent_result for each accepted result_id. Apply its depends_on results first. "
    "Use reject_agent_result with a reason for irrelevant or invalid work, then send_input for corrections if needed. "
    "Apply each ready part while other children are still running so the user sees progress. "
    "Children only prepare private drafts; only your drawing tools and apply_agent_result modify the shared canvas. "
    "Use close_agent to pause work and resume_agent to continue saved work. A Final Agent receives new work via send_input. "
    "Do not finish your response while child work is Running or while accepted results still await submission. "
    "Tool errors require correction or an honest explanation. Never claim unsubmitted work has been drawn. "
    "For simple requests call drawing tools directly to minimize latency. Batch related creations with create_elements. "
    "Always use native editable Excalidraw objects. Text needs type=text, x, y, text and optional fontSize. "
    "For line, arrow and freedraw, points are absolute if x/y are omitted; otherwise points are relative to x/y. "
    "For update_elements use ids and patch. For move_elements use ids and dx/dy, or one id and absolute x/y. "
    "For continuous conversation, use existing IDs, selected_ids, prior conversation and Agent summaries to resolve references. "
    "Preserve unrelated artwork. Use inspect_scene with ids or a name/text query to find older objects outside the overview. "
    "For pen drawing use several freedraw strokes with ordered points. Coordinates around x=50..950,y=40..650 are visible. "
    "Your next context includes a fresh canvas checkpoint and compact Agent states; complete raw tool records remain in SQLite. "
    "task_progress is your durable work record: it lists dispatched inputs, submitted results and remaining work. "
    "Use only the exact result_id and agent_id values in that record or tool outputs. "
    "Submitted results already exist on the canvas and fulfill their recorded goals. Reuse their objects for edits. "
    "Once the requested composition is present and all chosen results are committed, return your final reply promptly. "
    "Reply briefly in the user's language after finishing the requested work."
)
CHILD_SYSTEM = (
    "You are an independent drawing child Agent with persistent private context. "
    "Prepare native editable Excalidraw actions for the assigned input by calling the provided drawing tools. "
    "Only the main Agent may create or control Agents. You cannot spawn, delegate, send input to or resume any Agent. "
    "Your tools operate on a private draft. The main Agent reviews your saved result before submitting it to the shared canvas. "
    "Use create_elements for related shapes to reduce model calls. Continue with more tools when the goal requires further edits. "
    "When every assigned requirement is present, return a brief final text describing your prepared work; then this run ends. "
    "Use the draft and successful tool outputs to recognize completed work and finish promptly. "
    "Your checkpoint lists already prepared work. Do not recreate shapes already present in the draft. "
    "Tool outputs contain stable object IDs for subsequent edits. Use exact IDs, readable names and the assigned layout region. "
    "Text needs x, y and text; omit default style properties. For paths omit x/y for absolute points, or include x/y with relative points. "
    "Never use SVG or edit unrelated objects. Use inspect_scene with ids or a name/text query when more object information is needed. "
    "Your context contains the goal, previous work summary, current draft and recent complete tool exchanges. "
    "The draft already includes their successful effects. Raw older calls remain in SQLite. "
    "Correct invalid tool parameters using the returned error. Reply in the user's language."
)


class DrawingAgents:
    def __init__(self, store: StudioStore, settings: ModelConfig):
        self.store = store
        self.settings = settings.model_copy(deep=True)
        self.gateway = ModelGateway(self.settings)

    def main_messages(self, task_id: str, request: str, document: dict,
                      history: list[dict], exchange: list[dict]) -> list[dict]:
        session_id = self.store.get_task(task_id)["session_id"]
        results = self.store.agents.task_results(session_id, task_id)
        prompt = {
            "request": request, "canvas": scene_context(document),
            "recent_conversation": [{"role": item["role"], "content": item["content"]} for item in history],
            "child_agents": self.store.agents.list_agents(session_id),
            "task_progress": {
                "inputs": self.store.agents.task_runs(session_id, task_id),
                "submitted_results": [result for result in results
                                      if result["committed_count"] == result["action_count"] and not result["rejected_reason"]],
                "rejected_results": [result for result in results if result["rejected_reason"]],
                "committed_actions": sum(result["committed_count"] for result in results),
            },
            "pending_results": [self.store.agents.result_summary(item)
                                for item in self.store.agents.pending_results(session_id, task_id=task_id)],
        }
        return [{"role": "system", "content": MAIN_SYSTEM},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}, *copy.deepcopy(exchange)]

    def child_messages(self, run: dict, agent: dict) -> list[dict]:
        prompt = {
            "agent_id": run["agent_id"], "input_id": run["input_id"], "run_id": run["id"],
            "goal": run["message"], "previous_work_summary": agent["summary"],
            "scene": scene_context({**run["document"], "elements": run["draft"], "selection": run["selection"]}),
            "relevant_ids": run["relevant_ids"], "prepared_action_count": len(run["prepared"]),
        }
        if run["relevant_ids"]:
            prompt["relevant_elements"] = scene_context(
                {**run["document"], "elements": run["draft"], "selection": run["selection"]}, run["relevant_ids"],
            )["elements"]
        return [{"role": "system", "content": CHILD_SYSTEM},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                *copy.deepcopy(run["conversation"])]

    async def respond(self, task_id: str, agent_id: str, round_number: int,
                      messages: list[dict], *, child: bool = False) -> dict:
        started = time.perf_counter()
        response = await self.gateway.complete(
            messages, DRAWING_TOOLS if child else MAIN_TOOLS,
            on_retry=lambda info: self.store.log_tool(task_id, agent_id, round_number, "model_retry", info),
        )
        message = response.choices[0].message.model_dump(exclude_none=True)
        self.store.log_tool(task_id, agent_id, round_number, "llm_response", {
            "message": message, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "usage": response.usage.model_dump() if response.usage else None,
        })
        return message

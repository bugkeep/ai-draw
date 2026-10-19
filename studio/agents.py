import copy
import hashlib
import json
import time
from collections import Counter
from typing import Any

from shapely import box

from .actions import apply_action, create_native_element, element_fingerprint, validate_action
from .agent_tools import CHILD_TOOL_NAMES, CHILD_TOOLS, MAIN_TOOLS, validate_tool
from .llm import ModelGateway
from .eraser import element_geometry, filled_path
from .pen import path_elements
from .settings import ModelConfig
from .prompts import MAIN_SYSTEM, CHILD_SYSTEM
from .store import StudioStore


TOOL_DEFINITIONS = CHILD_TOOLS


def compact_drawing_exchange(turn: list[dict]) -> list[dict]:
    if not turn or turn[0].get('role') != 'assistant':
        return turn
    calls = turn[0].get('tool_calls', [])
    outputs = [item for item in turn[1:] if item.get('role') == 'tool']
    if (not calls or len(outputs) != len(calls) or len(turn) != len(outputs) + 1
            or {call['id'] for call in calls} != {item['tool_call_id'] for item in outputs}):
        return turn
    by_id = {item['tool_call_id']: item for item in outputs}
    archived = [call for call in calls if len(call['function']['arguments']) >= 2000
                and json.loads(by_id[call['id']]['content']).get('ok') is True]
    if not archived:
        return turn
    archived_ids = {call['id'] for call in archived}
    checkpoint = {'completed_drawing_tools': [{
        'name': call['function']['name'], 'call_id': call['id'], 'arguments_record': 'SQLite agent_tool_calls',
    } for call in archived], 'function_call_outputs': [{
        'type': 'function_call_output', 'call_id': item['tool_call_id'], 'output': item['content'],
    } for item in outputs if item['tool_call_id'] in archived_ids], 'instruction': 'These tools succeeded; drawing effects are already in the current canvas/draft. '
                                         'Full arguments remain in SQLite; use current object IDs and images for further edits.'}
    if turn[0].get('content'):
        checkpoint['assistant_note'] = turn[0]['content']
    result = [{'role': 'user', 'content': json.dumps(checkpoint, ensure_ascii=False)}]
    remaining = [call for call in calls if call['id'] not in archived_ids]
    if remaining:
        result.extend([{**turn[0], 'tool_calls': remaining},
                       *[item for item in outputs if item['tool_call_id'] not in archived_ids]])
    return result


def recent_tool_exchanges(turns: list[list[dict]], character_budget: int = 16_000) -> list[dict]:
    selected, size = [], 0
    for turn in reversed(turns):
        turn = compact_drawing_exchange(turn)
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
        raise ValueError("Child agents can only use drawing and reference tools and cannot create or delegate to other agents")


def scene_context(document: dict[str, Any], ids: list[str] | None = None, query: str = "",
                  include_geometry: bool = False, region: dict | None = None,
                  offset: int = 0, limit: int | None = None, expected_scene_state: str = '') -> dict[str, Any]:
    elements = [element for element in document["elements"] if not element.get("isDeleted")]
    page = {}
    if limit is not None:
        state = hashlib.sha256(json.dumps([document['id'], elements, document['selection'], ids or [], query, region],
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        if expected_scene_state and expected_scene_state != state:
            raise ValueError('Scene changed or inspection filters differ; inspect again from offset 0 to obtain current object IDs')
    if ids:
        by_id = {element["id"]: element for element in elements}
        if set(ids) - set(by_id):
            missing = sorted(set(ids) - set(by_id))
            raise ValueError(f"Requested scene objects do not exist: {missing}; inspect the current region or name to refresh object IDs")
        chosen = [by_id[element_id] for element_id in ids]
    elif query or region or limit is not None:
        chosen = elements
    else:
        selected = set(document['selection'])
        chosen = [element for element in elements if element['id'] in selected][:12]
        if len(chosen) < 12:
            chosen.extend([element for element in elements if element['id'] not in selected][-(12 - len(chosen)):])
        page = {'overview_only': True, 'shown_count': len(chosen), 'has_more': len(chosen) < len(elements),
                'first_page': {'offset': 0, 'limit': 12},
                'instruction': 'This overview contains selected and recent objects. Use inspect_scene pages, a region, '
                    'or a name query to locate other current objects. Missing from the overview does not mean missing from the drawing.'}
    if query:
        chosen = [element for element in chosen if query.casefold() in
                  f"{element.get('customData', {}).get('name', '')} {element.get('text', '')}".casefold()]
    if region:
        area = box(region['x'], region['y'], region['x'] + region['width'], region['y'] + region['height'])
        chosen = [element for element in chosen if element_geometry(element).intersects(area)]
    if limit is not None:
        total = len(chosen)
        next_offset = offset + limit if offset + limit < total else None
        page = {'scene_state': state, 'matching_count': total, 'offset': offset,
                'returned_count': len(chosen[offset:offset + limit]), 'next_offset': next_offset,
                'next_page': {'ids': ids or [], 'query': query, 'region': region,
                    'include_geometry': include_geometry, 'offset': next_offset, 'limit': limit,
                    'expected_scene_state': state} if next_offset is not None else None}
        chosen = chosen[offset:offset + limit]
    return {
        "document_id": document["id"], "version": document["version"],
        "element_count": len(elements), "selected_ids": document["selection"],
        'type_counts': dict(Counter(element['type'] for element in elements)),
        **page,
        "elements": [
            {"id": element["id"], "name": element.get("customData", {}).get("name", ""),
             "type": element["type"], "x": element["x"], "y": element["y"],
             "width": element.get("width"), "height": element.get("height"),
             "angle": element.get("angle", 0),
             "strokeColor": element.get("strokeColor"), "backgroundColor": element.get("backgroundColor"),
             "strokeWidth": element.get("strokeWidth"), "opacity": element.get("opacity"),
             "text": str(element.get("text", ""))[:120], "version": element.get("version", 1),
             **({"point_count": len(element["points"])} if "points" in element else {}),
             **({"points": element["points"]} if include_geometry and "points" in element else {})}
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
    if args.get('observation_id'):
        raise ValueError('Resolve observed image coordinates before preparing canvas actions')
    if name == "inspect_scene":
        return draft, selection, [], {"ok": True, "scene": scene_context(
            {**document, "elements": draft, "selection": selection}, args.get("ids"), args.get("query", ""),
            args.get("include_geometry", False), args.get("region"), args.get('offset', 0), args.get('limit', 12),
            args.get('expected_scene_state', ''))}
    if name == "reshape_path":
        existing = next((element for element in draft if element["id"] == args["element_id"]
                         and not element.get("isDeleted")), None)
        if existing is None or existing["type"] not in {"line", "arrow", "freedraw"}:
            raise ValueError("Requested native path does not exist")
        close_contour = args.get('closed', filled_path(existing))
        item = path_elements({"paths": [{"name": existing.get("customData", {}).get("name") or "画笔曲线",
                                        "points": args["points"], "smooth": args.get("smooth", True),
                                        "closed": close_contour}]})[0]
        replacement = create_native_element(item)
        name, args = "update_elements", {"ids": [existing["id"]], "patch": {
            key: replacement[key] for key in ("x", "y", "width", "height", "points", "angle")}}
        if filled_path(existing) and not close_contour:
            args['patch']['backgroundColor'] = 'transparent'
    if name in {"create_elements", "draw_paths"}:
        items = path_elements(args) if name == "draw_paths" else args["items"]
        draft, selection, actions, changed_ids = prepare_creation_batch(items, draft, selection)
    else:
        action = validate_action({"kind": name, "args": args})
        by_id = {element["id"]: element for element in draft}
        expected = {element_id: {"version": int(by_id[element_id].get("version", 1)),
                                 "fingerprint": element_fingerprint(by_id[element_id])}
                    for element_id in args.get("ids", []) if element_id in by_id}
        draft, selection, changed_ids = apply_action(draft, selection, action)
        if name == "erase_strokes":
            expected = {element_id: {"version": int(by_id[element_id].get("version", 1)),
                                     "fingerprint": element_fingerprint(by_id[element_id])}
                        for element_id in changed_ids if element_id in by_id}
            # Pin the edited objects without changing a spatial erase into an
            # explicit hidden-object erase when the prepared action is replayed.
            action['args']['visible_only'] = not args.get('ids') or args.get('visible_only', False)
            action['args']['ids'] = list(expected)
        if name == "create_element":
            action["element_id"] = changed_ids[0]
        actions = [{"action": action, "expected_elements": expected}] if name != 'erase_strokes' or changed_ids else []
    return draft, selection, actions, {"ok": True, "changed_ids": changed_ids,
                                       "objects": [{"id": element["id"], "name": element.get("customData", {}).get("name", "")}
                                                   for element in draft if element["id"] in set(changed_ids)],
                                       "selected_ids": selection, "action_count": len(actions)}



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
            "research_references": self.store.research_references(task_id),
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
            live_ids = {item['id'] for item in run['draft'] if not item.get('isDeleted')}
            relevant_ids = [element_id for element_id in run['relevant_ids'] if element_id in live_ids]
            prompt["relevant_elements"] = scene_context(
                {**run["document"], "elements": run["draft"], "selection": run["selection"]}, relevant_ids,
            )["elements"] if relevant_ids else []
            prompt['removed_relevant_ids'] = [element_id for element_id in run['relevant_ids'] if element_id not in live_ids]
        return [{"role": "system", "content": CHILD_SYSTEM},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                *copy.deepcopy(run["conversation"])]

    async def respond(self, task_id: str, agent_id: str, round_number: int,
                      messages: list[dict], *, child: bool = False,
                      observation_ids: list[str] | None = None,
                      reference_image_ids: list[str] | None = None,
                      result_observation_ids: list[str] | None = None,
                      quality_review_keys: list[str] | None = None) -> dict:
        started = time.perf_counter()
        session_id = self.store.get_task(task_id)["session_id"]
        document_id = self.store.session_document_id(session_id)

        def model_state(info: dict) -> None:
            self.store.publish(document_id, "phase", {"task_id": task_id, "agent_id": agent_id, **info})

        def model_retry(info: dict) -> None:
            self.store.log_tool(task_id, agent_id, round_number, "model_retry", info)
            model_state({"phase": "model_retry", **{key: value for key, value in info.items() if key != "message"}})

        outputs = [item for item in messages if item.get("role") == "tool"]
        text_messages = [{**item, 'content': [part for part in item['content'] if part.get('type') == 'text']}
                         if isinstance(item.get('content'), list) else item for item in messages]
        self.store.log_tool(task_id, agent_id, round_number, "model_context", {
            "message_count": len(messages), "character_count": len(json.dumps(text_messages, ensure_ascii=False)),
            "image_observation_ids": observation_ids or [],
            "result_observation_ids": result_observation_ids or [],
            "quality_review_keys": quality_review_keys or [],
            "reference_image_view_ids": reference_image_ids or [],
            "tool_output_ids": [item["tool_call_id"] for item in outputs],
            "tool_error_ids": [item["tool_call_id"] for item in outputs
                               if json.loads(item["content"]).get("ok") is False],
        })
        response = await self.gateway.complete(
            messages, CHILD_TOOLS if child else MAIN_TOOLS,
            on_retry=model_retry, on_state=model_state,
        )
        message = response.choices[0].message.model_dump(exclude_none=True)
        self.store.log_tool(task_id, agent_id, round_number, "llm_response", {
            "message": message, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "finish_reason": response.choices[0].finish_reason,
            "usage": response.usage.model_dump() if response.usage else None,
        })
        return message

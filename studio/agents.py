import copy
import json
import time
from typing import Any

from pydantic import BaseModel, Field

from .actions import apply_action, element_fingerprint, validate_action
from .llm import ModelGateway
from .settings import ModelConfig
from .store import StudioStore


class Delegation(BaseModel):
    id: str = Field(min_length=1, max_length=30)
    goal: str = Field(min_length=1, max_length=600)
    relevant_ids: list[str] = Field(default_factory=list, max_length=30)


class Plan(BaseModel):
    reply: str = Field(default="", max_length=1000)
    actions: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    delegations: list[Delegation] = Field(default_factory=list, max_length=4)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "create_element", "description": "Prepare one native Excalidraw element. For line, arrow and freedraw, points are absolute if x and y are omitted; when x or y is supplied, points are relative to that origin. pointSpace can select either rule explicitly.",
            "parameters": {
                "type": "object", "properties": {
                    "type": {"type": "string", "enum": ["rectangle", "ellipse", "diamond", "text", "line", "arrow", "freedraw"]},
                    "x": {"type": "number"}, "y": {"type": "number"},
                    "width": {"type": "number"}, "height": {"type": "number"},
                    "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}},
                    "pointSpace": {"type": "string", "enum": ["absolute", "relative"]},
                    "text": {"type": "string"}, "name": {"type": "string"},
                    "strokeColor": {"type": "string"}, "backgroundColor": {"type": "string"},
                    "strokeWidth": {"type": "number"}, "fontSize": {"type": "number"},
                    "roughness": {"type": "integer"}, "opacity": {"type": "integer"},
                }, "required": ["type"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_elements", "description": "Select existing element IDs for the next edit or to show the user.",
            "parameters": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                           "required": ["ids"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_elements", "description": "Move existing elements by dx and dy canvas units.",
            "parameters": {"type": "object", "properties": {
                "ids": {"type": "array", "items": {"type": "string"}}, "dx": {"type": "number"}, "dy": {"type": "number"}},
                "required": ["ids", "dx", "dy"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_elements", "description": "Change supported element properties, including text, size and colors.",
            "parameters": {"type": "object", "properties": {
                "ids": {"type": "array", "items": {"type": "string"}},
                "patch": {"type": "object"}},
                "required": ["ids", "patch"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_elements", "description": "Delete existing elements by ID.",
            "parameters": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                           "required": ["ids"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_scene", "description": "Read current draft element IDs, names, types and positions.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]
TOOL_DEFINITIONS.insert(0, {
    "type": "function",
    "function": {
        "name": "create_elements", "description": "Prepare several independent native Excalidraw elements in one validated tool call.",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": TOOL_DEFINITIONS[0]["function"]["parameters"],
                      "minItems": 1, "maxItems": 24}},
            "required": ["items"], "additionalProperties": False},
    },
})


def scene_context(document: dict[str, Any]) -> dict[str, Any]:
    elements = [element for element in document["elements"] if not element.get("isDeleted")]
    selected_ids = set(document["selection"])
    chosen = elements[-70:]
    for element in elements:
        if element["id"] in selected_ids and element not in chosen:
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


class DrawingAgents:
    def __init__(self, store: StudioStore, settings: ModelConfig):
        self.store = store
        self.settings = settings
        self.gateway = ModelGateway(settings)

    async def plan(self, task_id: str, user_text: str, document: dict, messages: list[dict]) -> Plan:
        system = (
            "You are the main agent of a conversational Excalidraw drawing editor. "
            "Respond with one JSON object with keys reply, actions, delegations. "
            "Use native editable Excalidraw elements only. No SVG or code output. "
            "Action format: {kind: create_element|select_elements|move_elements|update_elements|delete_elements, args: {...}}. "
            "Existing-object examples: {kind:'move_elements',args:{ids:['id'],dx:70,dy:0}} and "
            "{kind:'update_elements',args:{ids:['id'],patch:{backgroundColor:'#2f9e44'}}}. "
            "Always use ids and patch for update_elements; never use an elements array. "
            "For create_element use type, x, y, width, height, name, colors; text needs text. "
            "For line, arrow and freedraw, omit x and y when points are absolute; when x or y is supplied, points are relative to that origin. "
            "For move_elements use ids with dx and dy offsets, or one id with absolute x and y. "
            "For simple requests return direct actions and no delegations to minimize latency. "
            "For a composition with independent parts, delegate at most three focused goals. "
            "Delegations each have id, goal, relevant_ids. Child agents prepare actions concurrently; you will review their JSON reports. "
            "For edits, refer to exact existing element IDs. Prefer selected_ids when the user says this or it. "
            "Preserve existing artwork unless explicitly asked to change it. "
            "For a request to draw by pen, use multiple freedraw elements with 12-80 absolute points each so the drawing process is visible. "
            "Use coordinates in the visible canvas around x=50..950 and y=40..650. "
            "Reply and names should be in the user's language. Do not include markdown fences."
        )
        history = [{"role": item["role"], "content": item["content"]} for item in messages[-10:]
                   if item["role"] in {"user", "assistant"}]
        prompt = {"request": user_text, "canvas": scene_context(document), "recent_conversation": history}
        conversation = [{"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]
        for attempt in range(2):
            started = time.perf_counter()
            response = await self.gateway.complete(conversation)
            content = response.choices[0].message.content or ""
            self.store.log_tool(task_id, "main", attempt, "plan", {
                "response": content, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "usage": response.usage.model_dump() if response.usage else None,
            })
            try:
                plan = Plan.model_validate(json.loads(content))
                if len(plan.delegations) > 3:
                    raise ValueError("Main agent requested too many child agents")
                if not plan.actions and not plan.delegations:
                    raise ValueError("Main agent returned no drawing actions")
                for action in plan.actions:
                    validate_action(action)
                return plan
            except (ValueError, KeyError) as exc:
                if attempt:
                    raise
                conversation.extend([
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"Correct the JSON action arguments and return the full plan again. Validation error: {str(exc)[:1200]}"},
                ])
        raise RuntimeError("Main agent did not produce a valid plan")

    async def child(self, task_id: str, delegation: Delegation, document: dict) -> dict[str, Any]:
        agent_id = f"child:{delegation.id}"
        draft = copy.deepcopy(document["elements"])
        selection = list(document["selection"])
        prepared: list[dict[str, Any]] = []
        system = (
            "You are a drawing child agent. Your only job is to prepare validated Excalidraw actions for the assigned goal. "
            "Call the provided drawing tools. For a composition, use create_elements once with all necessary shapes to reduce latency. "
            "Use canvas coordinates, existing IDs from context, and readable names. "
            "For paths, omit x and y for absolute points, or provide x and y with relative points. "
            "For pen drawing, create freedraw elements with ordered points; use several strokes when needed. "
            "Never use SVG. Never edit unrelated existing elements. "
            "The runtime returns your validated tool actions as JSON to the main agent. "
            "Your tool responses show created element IDs for any following edits."
        )
        content = {"goal": delegation.goal, "scene": scene_context(document), "relevant_ids": delegation.relevant_ids}
        conversation: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
        ]
        for round_number in range(1, 4):
            started = time.perf_counter()
            response = await self.gateway.complete(conversation, TOOL_DEFINITIONS)
            message = response.choices[0].message
            self.store.log_tool(task_id, agent_id, round_number, "llm_response", {
                "content": message.content,
                "tool_calls": [{"id": call.id, "name": call.function.name, "arguments": call.function.arguments}
                               for call in message.tool_calls or []],
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "usage": response.usage.model_dump() if response.usage else None,
            })
            conversation.append(message.model_dump(exclude_none=True))
            if not message.tool_calls:
                raise ValueError(f"Child agent {delegation.id} called no drawing tools")
            had_error = False
            for call in message.tool_calls:
                if len(prepared) >= 24:
                    raise ValueError(f"Child agent {delegation.id} exceeded its action limit")
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments)
                    if name == "inspect_scene":
                        result = {"scene": scene_context({**document, "elements": draft, "selection": selection})}
                    elif name == "create_elements":
                        items = args["items"]
                        if not isinstance(items, list) or not 1 <= len(items) <= 24:
                            raise ValueError("create_elements needs 1 to 24 items")
                        if len(prepared) + len(items) > 24:
                            raise ValueError("Child agent exceeded its action limit")
                        validated = [validate_action({"kind": "create_element", "args": item}) for item in items]
                        current_draft, current_selection = draft, selection
                        batch: list[dict[str, Any]] = []
                        created_ids = []
                        for action in validated:
                            current_draft, current_selection, changed_ids = apply_action(current_draft, current_selection, action)
                            action["element_id"] = changed_ids[0]
                            batch.append({"action": action, "expected_elements": {}})
                            created_ids.extend(changed_ids)
                        draft, selection = current_draft, current_selection
                        prepared.extend(batch)
                        result = {"ok": True, "changed_ids": created_ids, "selected_ids": selection}
                    else:
                        action = validate_action({"kind": name, "args": args})
                        current = {element["id"]: element for element in draft}
                        expected = {element_id: {"version": int(current[element_id].get("version", 1)),
                                                 "fingerprint": element_fingerprint(current[element_id])}
                                    for element_id in args.get("ids", []) if element_id in current}
                        draft, selection, changed_ids = apply_action(draft, selection, action)
                        if name == "create_element":
                            action["element_id"] = changed_ids[0]
                        prepared.append({"action": action, "expected_elements": expected})
                        result = {"ok": True, "changed_ids": changed_ids, "selected_ids": selection}
                except (ValueError, KeyError, TypeError) as exc:
                    result = {"ok": False, "error": str(exc)}
                    had_error = True
                self.store.log_tool(task_id, agent_id, round_number, name, {"arguments": call.function.arguments, "result": result})
                conversation.append({"role": "tool", "tool_call_id": call.id,
                                     "content": json.dumps(result, ensure_ascii=False)})
            if prepared and not had_error:
                names = [item["action"]["args"].get("name", item["action"]["kind"])
                         for item in prepared]
                return {"id": delegation.id, "goal": delegation.goal,
                        "summary": f"Prepared {len(prepared)} actions: {', '.join(names[:12])}",
                        "actions": prepared, "tool_call_count": len(prepared), "success": True}
        raise RuntimeError(f"Child agent {delegation.id} did not prepare valid actions")

    async def review(self, task_id: str, request: str, reports: list[dict[str, Any]]) -> tuple[list[str], str]:
        system = (
            "You are the main drawing agent reviewing child agent JSON reports. "
            "Each child prepared actions on an isolated scene. The executor will commit accepted reports one by one with version checks. "
            "Return JSON with accepted_ids (ordered list of report IDs) and reply (brief user-facing summary). "
            "Accept only successful reports relevant to the user's request. Do not request new drawing actions."
        )
        def action_outline(item: dict[str, Any]) -> dict[str, Any]:
            action = item["action"]
            args = action["args"]
            if action["kind"] == "create_element":
                fields = ("type", "name", "x", "y", "width", "height", "strokeColor", "backgroundColor")
                outline = {field: args[field] for field in fields if field in args}
                if "points" in args:
                    outline["point_count"] = len(args["points"])
            else:
                outline = {field: args[field] for field in ("ids", "dx", "dy", "x", "y", "patch") if field in args}
            return {"kind": action["kind"], "args": outline}

        compact = [{"id": report["id"], "goal": report["goal"], "summary": report.get("summary", ""),
                    "success": report.get("success", False), "error": report.get("error", ""),
                    "actions": [action_outline(item) for item in report.get("actions", [])]}
                   for report in reports]
        started = time.perf_counter()
        response = await self.gateway.complete([
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"request": request, "reports": compact}, ensure_ascii=False)},
        ])
        content = response.choices[0].message.content or ""
        self.store.log_tool(task_id, "main", 0, "review", {
            "response": content, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "usage": response.usage.model_dump() if response.usage else None,
        })
        parsed = json.loads(content)
        ids = parsed["accepted_ids"]
        available = {report["id"] for report in reports if report.get("success")}
        if not isinstance(ids, list) or len(ids) != len(set(ids)) or set(ids) - available:
            raise ValueError("Main agent selected invalid child results")
        return ids, str(parsed.get("reply", ""))[:1000]

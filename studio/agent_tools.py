from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .actions import CreateElement, DeleteElements, MoveElements, SelectElements, UpdateElements


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SpawnAgent(ToolInput):
    message: str = Field(min_length=1)
    nickname: str = Field(default="", max_length=100)
    relevant_ids: list[str] = Field(default_factory=list)


class SendInput(ToolInput):
    agent_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    relevant_ids: list[str] = Field(default_factory=list)


class WaitAgent(ToolInput):
    ids: list[str] = Field(min_length=1)
    after: dict[str, int] = Field(default_factory=dict)


class AgentIdentity(ToolInput):
    agent_id: str = Field(min_length=1)


class ApplyAgentResult(ToolInput):
    result_id: str = Field(min_length=1)


class RejectAgentResult(ApplyAgentResult):
    reason: str = Field(min_length=1)


class CreateElements(ToolInput):
    items: list[CreateElement] = Field(min_length=1)


class InspectScene(ToolInput):
    ids: list[str] = Field(default_factory=list)
    query: str = ""


DRAWING_INPUTS = {
    "create_elements": CreateElements,
    "create_element": CreateElement,
    "select_elements": SelectElements,
    "move_elements": MoveElements,
    "update_elements": UpdateElements,
    "delete_elements": DeleteElements,
    "inspect_scene": InspectScene,
}
LIFECYCLE_INPUTS = {
    "spawn_agent": SpawnAgent, "send_input": SendInput, "wait_agent": WaitAgent,
    "close_agent": AgentIdentity, "resume_agent": AgentIdentity,
    "apply_agent_result": ApplyAgentResult,
    "reject_agent_result": RejectAgentResult,
}
CHILD_TOOL_NAMES = frozenset(DRAWING_INPUTS)
MAIN_TOOL_NAMES = CHILD_TOOL_NAMES | frozenset(LIFECYCLE_INPUTS)


def definition(name: str, description: str, model: type[BaseModel]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": model.model_json_schema(),
    }}


DRAWING_TOOLS = [
    definition("create_elements", "Create a batch of native editable Excalidraw elements. Use one call for related shapes. Text only needs type, x, y and text. For line, arrow or freedraw, points are absolute when x and y are omitted; otherwise points are relative to that origin.", CreateElements),
    definition("create_element", "Create one native Excalidraw element. For paths use absolute points without x/y, or relative points with x/y. Give objects readable names.", CreateElement),
    definition("select_elements", "Select existing object IDs for subsequent edits or to show the user.", SelectElements),
    definition("move_elements", "Move objects by ids and dx/dy, or move one object to absolute x/y.", MoveElements),
    definition("update_elements", "Update existing ids using patch, including text, position, size and colors.", UpdateElements),
    definition("delete_elements", "Delete existing objects by ids.", DeleteElements),
    definition("inspect_scene", "Read current objects, selection and canvas revision. Optional ids requests specific objects outside the overview. Optional query searches all object names and text, including older objects omitted from the overview.", InspectScene),
]
LIFECYCLE_TOOLS = [
    definition("spawn_agent", "Start a persistent child Agent asynchronously. Return agent_id, nickname, input_id and run_id immediately. Assign a self-contained drawing goal with shared layout bounds and connector positions. Children only prepare drawing actions.", SpawnAgent),
    definition("send_input", "Send new work to an existing Agent. Running Agents queue inputs in arrival order. Final Agents become Running. Each input gets its own input_id and run_id. Resume a Shutdown Agent before sending input.", SendInput),
    definition("wait_agent", "Observe or wait for Agents in this canvas. Return when any requested Agent is Final/Shutdown or has a new completed run. For another wait, use only returned running_ids and their previous revisions in after. Final/Shutdown always return immediately. This tool never starts or resumes work and has no execution deadline.", WaitAgent),
    definition("close_agent", "Stop a child Agent and retain its context, pending inputs and prepared drawing progress. State becomes Shutdown. No drawing actions are submitted by closing.", AgentIdentity),
    definition("resume_agent", "Resume a Shutdown Agent with the same agent_id and saved progress. Continue unfinished inputs. An Agent without unfinished work becomes Final. Completed work is never restarted.", AgentIdentity),
    definition("apply_agent_result", "Approve and submit one saved child drawing result to this canvas, step by step. Review its summary first. Apply listed depends_on results first. Repeating this call skips actions already committed.", ApplyAgentResult),
    definition("reject_agent_result", "Reject unsubmitted actions from a child result with an explanation. Already committed actions stay on the canvas. Use send_input to request corrected work when needed.", RejectAgentResult),
]
MAIN_TOOLS = DRAWING_TOOLS + LIFECYCLE_TOOLS


def validate_tool(name: str, arguments: Any, *, child: bool = False) -> dict:
    models = DRAWING_INPUTS if child else {**DRAWING_INPUTS, **LIFECYCLE_INPUTS}
    if name not in models:
        if child:
            raise ValueError("Child agents can only use drawing tools and cannot create or delegate to other agents")
        raise ValueError(f"Unknown main Agent tool: {name}")
    parsed = models[name].model_validate(arguments)
    return parsed.model_dump(exclude_unset=True)

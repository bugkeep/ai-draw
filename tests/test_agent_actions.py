import pytest

from studio.actions import validate_action
from studio.agents import CHILD_TOOL_NAMES, TOOL_DEFINITIONS, Delegation, Plan, prepare_creation_batch, validate_child_tool
from studio.errors import validation_summary
from studio.store import StudioStore


def test_complex_plan_and_child_batch_preserve_all_actions(tmp_path):
    items = [{"type": "rectangle", "x": 20 + (index % 9) * 90, "y": 20 + (index // 9) * 60,
              "width": 80, "height": 50, "name": f"节点 {index}"} for index in range(121)]
    plan = Plan.model_validate({"actions": [{"kind": "create_element", "args": item} for item in items]})
    assert len(plan.actions) == 121
    goal = "完整的区域绘图要求和连接点说明。" * 50
    assert Delegation(id="diagram", goal=goal).goal == goal
    draft, selection, prepared, created_ids = prepare_creation_batch(items, [], [])
    assert len(draft) == len(prepared) == len(created_ids) == 121
    assert len(set(created_ids)) == 121 and selection == [created_ids[-1]]
    store = StudioStore(tmp_path / "complex.sqlite3")
    session = store.create_session("连续创建与编辑")
    document_id = session["document"]["id"]
    for item in prepared:
        store.commit_action(document_id, item["action"], item["expected_elements"])
    document = store.get_document(document_id)
    assert [item["id"] for item in document["elements"]] == created_ids
    assert document["version"] == 121
    store.commit_action(document_id, {"kind": "move_elements", "args": {"ids": created_ids, "dx": 5, "dy": 10}}, {})
    assert store.history_step(document_id, "undo")["elements"] == document["elements"]
    assert store.history_step(document_id, "redo")["elements"][0]["x"] == 25
    store.close()


def test_batch_validation_is_atomic_and_diagnostics_exclude_input():
    items = [{"type": "rectangle", "name": f"节点 {index}"} for index in range(40)]
    items.append({"type": "text", "text": "sensitive-input", "fontSize": -1})
    draft = []
    with pytest.raises(ValueError) as caught:
        prepare_creation_batch(items, draft, [])
    assert draft == []
    message = validation_summary(caught.value)
    assert "fontSize" in message and "sensitive-input" not in message
    assert "input_value" not in message and "https://errors.pydantic.dev" not in message
    with pytest.raises(ValueError):
        validate_action({"kind": "create_element", "args": {"type": "line", "points": [[1, 2]]}})


def test_only_main_can_delegate_and_child_tools_cannot_create_agents():
    plan = Plan(delegations=[Delegation(id=f"section-{index}", goal=f"绘制区域 {index}") for index in range(6)])
    assert len(plan.delegations) == 6
    with pytest.raises(ValueError, match="unique ID"):
        Plan(delegations=[Delegation(id="same", goal="左侧"), Delegation(id="same", goal="右侧")])
    assert {tool["function"]["name"] for tool in TOOL_DEFINITIONS} == CHILD_TOOL_NAMES
    for name in CHILD_TOOL_NAMES:
        validate_child_tool(name)
    for name in ("spawn_agent", "create_agent", "delegate", "dispatch_task"):
        with pytest.raises(ValueError, match="cannot create or delegate"):
            validate_child_tool(name)

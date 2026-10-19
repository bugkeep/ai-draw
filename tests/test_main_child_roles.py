import asyncio
import json

import pytest

from studio.agent_tools import CHILD_TOOLS, MAIN_TOOLS, validate_tool
from studio.agents import DrawingAgents
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def workspace(tmp_path):
    store = StudioStore(tmp_path / 'roles.sqlite3')
    runtime = DrawingRuntime(store, ModelSettings(store))
    session = store.create_session('主 Agent 与绘图子 Agent')
    task = store.create_task(session['id'])
    models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
    return store, runtime, session, task, models


async def main_tool(runtime, models, session, task, name, arguments, call_id):
    return await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'],
        {'id': call_id, 'function': {'name': name, 'arguments': json.dumps(arguments)}})


def test_role_specific_schemas_keep_fine_pen_tools_in_children_and_control_tools_in_main():
    main = {item['function']['name']: item['function'] for item in MAIN_TOOLS}
    child = {item['function']['name']: item['function'] for item in CHILD_TOOLS}
    assert {'draw_paths', 'reshape_path'} <= child.keys()
    assert not {'draw_paths', 'reshape_path'} & main.keys()
    assert {'spawn_agent', 'send_input', 'apply_agent_result'} <= main.keys()
    assert not {'spawn_agent', 'send_input', 'apply_agent_result'} & child.keys()
    assert 'freedraw' not in main['create_element']['parameters']['properties']['type']['enum']
    assert 'freedraw' in child['create_element']['parameters']['properties']['type']['enum']
    assert 'trace_reference' in main and 'continue_reference_trace' in child


@pytest.mark.parametrize('name,args', [
    ('draw_paths', {'paths': [{'name': '曲线', 'points': [[0, 0], [20, 10], [40, 0]]}]}),
    ('reshape_path', {'element_id': 'old', 'points': [[0, 0], [20, 20]]}),
    ('create_element', {'type': 'freedraw', 'points': [[0, 0], [30, 20]]}),
    ('create_elements', {'items': [{'type': 'freedraw', 'points': [[0, 0], [30, 20]]}]}),
])
def test_main_pen_attempt_is_rejected_without_canvas_changes_and_full_error_is_retained(tmp_path, name, args):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path)
        try:
            before = store.get_document(session['document']['id'])
            output = await main_tool(runtime, models, session, task, name, args, 'misrouted')
            assert output['ok'] is False and 'child Agent' in output['error']
            assert store.get_document(before['id']) == before
            assert store.get_history(before['id'], 'undo') == []
            assert store.connection.execute('SELECT count(*) FROM drawing_results').fetchone()[0] == 0
            messages = runtime.memory.prepare_messages(task['id'], 'main', f"main:{task['id']}", 'state', before, [])
            assert any(item.get('tool_call_id') == 'misrouted' and json.loads(item['content']) == output for item in messages)
            assert validate_tool(name, args, child=True)
        finally:
            await runtime.close()
            store.close()

    asyncio.run(check())


def test_child_geometry_stays_private_and_main_can_submit_then_move_style_and_undo(tmp_path, native_drawing_child):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path)
        try:
            child = native_drawing_child(store, runtime, session, task)
            prepared = child.call('draw_paths', {'paths': [{'name': '原生轮廓',
                'points': [[0, 0], [30, 20], [60, 0]]}]}, 'contour')
            assert prepared['ok'] and store.get_document(session['document']['id'])['elements'] == []
            await child.submit()
            document = store.get_document(session['document']['id'])
            element_id = prepared['changed_ids'][0]
            refused = await main_tool(runtime, models, session, task, 'update_elements', {
                'ids': [element_id], 'patch': {'points': [[0, 0], [20, 15]]}}, 'curve-bypass')
            assert refused['ok'] is False and store.get_document(document['id']) == document
            moved = await main_tool(runtime, models, session, task, 'move_elements', {
                'ids': [element_id], 'dx': 17, 'dy': 23}, 'move')
            styled = await main_tool(runtime, models, session, task, 'update_elements', {
                'ids': [element_id], 'patch': {'strokeColor': '#ef4444'}}, 'style')
            assert moved['ok'] and styled['ok']
            final = store.get_document(document['id'])
            assert final['elements'][0]['id'] == element_id
            assert final['elements'][0]['points'] == document['elements'][0]['points']
            assert final['elements'][0]['x'] == document['elements'][0]['x'] + 17
            assert store.history_step(document['id'], 'undo')['elements'][0]['strokeColor'] == document['elements'][0]['strokeColor']
            assert store.history_step(document['id'], 'redo')['elements'] == final['elements']
        finally:
            await runtime.close()
            store.close()

    asyncio.run(check())

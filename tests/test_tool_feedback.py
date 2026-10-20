import copy
import hashlib
import json
from pathlib import Path

import pytest

from studio.agent_tools import validate_tool
from studio.memory import AgentMemory, bounded_summary, tool_summary
from studio.store import StudioStore
from studio.tool_feedback import image_coordinate_feedback


def receipts():
    return json.loads((Path(__file__).parent / 'fixtures/coordinate-feedback-receipts.json').read_text(encoding='utf-8'))['cases']


def test_recorded_successful_image_calls_keep_coordinate_extents_without_rewriting_input_or_output():
    for case in receipts()[1:]:
        assert hashlib.sha256(case['arguments'].encode()).hexdigest() == case['arguments_sha256']
        arguments = json.loads(case['arguments'])
        validate_tool(case['name'], arguments, child=True)
        original = copy.deepcopy((arguments, case['output']))
        result = tool_summary(case['name'], case['output'], arguments)
        coordinates = result['input_coordinates']
        assert coordinates['axis_range'] == [0, 1000]
        assert coordinates['point_count'] == sum(len(item['points']) for item in arguments['paths'])
        if case['case'] == 'tiny-image-anchors':
            assert 0 <= coordinates['x'][0] <= coordinates['x'][1] < 1
            assert 0 <= coordinates['y'][0] <= coordinates['y'][1] < 1
            assert '0.1%' in coordinates['instruction']
        else:
            assert coordinates['x'][1] > 1 and coordinates['y'][1] > 1
            assert 'instruction' not in coordinates
        assert (arguments, case['output']) == original


def test_current_feedback_and_retrieved_summary_keep_units_and_full_original_receipts(tmp_path):
    store = StudioStore(tmp_path / 'feedback.sqlite3')
    memory = AgentMemory(store)
    session = store.create_session()
    task = store.create_task(session['id'])
    caller = f"main:{task['id']}"
    try:
        for case in receipts():
            store.agents.begin_call(task['id'], caller, case['call_id'], case['name'], case['arguments'])
            store.agents.finish_call(caller, case['call_id'], case['output'])
            context = memory.for_context(task['id'], 'main', caller, case['call_id'], case['name'],
                case['arguments'], case['output'], 'receipt-replay')
            if not case['output']['ok']:
                assert context == {**case['output'], 'memory_ref': context['memory_ref']}
            else:
                summary = memory.recall(task['id'], 'main', {'memory_ref': context['memory_ref']}, session['document'])['summary']
                assert summary['input_coordinates'] == context.get('input_coordinates', context.get('summary', {}).get('input_coordinates'))
            saved = store.connection.execute('SELECT arguments,output FROM agent_tool_calls WHERE caller_id=? AND call_id=?',
                (caller, case['call_id'])).fetchone()
            assert saved['arguments'] == case['arguments'] and json.loads(saved['output']) == case['output']
        assert store.get_document(session['document']['id']) == session['document']
    finally:
        store.close()


def test_existing_receipt_refreshes_only_its_derived_summary(tmp_path):
    store = StudioStore(tmp_path / 'existing-feedback.sqlite3')
    memory = AgentMemory(store)
    session = store.create_session()
    task = store.create_task(session['id'])
    caller = f"main:{task['id']}"
    case = receipts()[1]
    try:
        store.agents.begin_call(task['id'], caller, case['call_id'], case['name'], case['arguments'])
        store.agents.finish_call(caller, case['call_id'], case['output'])
        reference = memory.register(task['id'], 'main', caller, case['call_id'], case['name'],
            case['arguments'], case['output'], 'original-state')
        with store.connection:
            store.connection.execute('UPDATE agent_memories SET summary=? WHERE id=?',
                (json.dumps(bounded_summary(case['output'])), reference))
        before = dict(store.connection.execute('SELECT * FROM agent_memories WHERE id=?', (reference,)).fetchone())
        context = memory.for_context(task['id'], 'main', caller, case['call_id'], case['name'],
            case['arguments'], case['output'], 'new-context-state')
        after = dict(store.connection.execute('SELECT * FROM agent_memories WHERE id=?', (reference,)).fetchone())
        assert context['memory_ref'] == reference
        assert 'input_coordinates' in json.loads(after['summary'])
        assert {key: value for key, value in before.items() if key != 'summary'} == {
            key: value for key, value in after.items() if key != 'summary'}
    finally:
        store.close()


@pytest.mark.parametrize('tool,arguments', [
    ('draw_paths', {'paths': [{'name': '合法微小路径', 'points': [[0.1, 0.2], [0.4, 0.8]]}]}),
    ('draw_paths', {'paths': [{'name': '像素路径', 'points': [[0.1, 0.2], [0.4, 0.8]]}],
        'observation_id': 'view_example', 'coordinate_space': 'image_pixels'}),
    ('move_elements', {'ids': ['existing'], 'dx': 0.1, 'dy': 0.2}),
])
def test_non_image_normalized_actions_keep_their_existing_feedback(tool, arguments):
    original = copy.deepcopy(arguments)
    assert image_coordinate_feedback(tool, arguments) == {}
    assert arguments == original


@pytest.mark.parametrize('tool', ['reshape_path', 'erase_strokes'])
def test_edit_and_erase_metadata_describes_the_selected_image_frame(tool):
    arguments = {'observation_id': 'view_example', 'coordinate_space': 'image_normalized', 'points': [[250, 300], [500, 650]]}
    original = copy.deepcopy(arguments)
    result = image_coordinate_feedback(tool, arguments)['input_coordinates']
    assert result['x'] == [250, 500] and result['y'] == [300, 650]
    assert result['point_count'] == 2 and 'instruction' not in result
    assert arguments == original

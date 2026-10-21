import asyncio
import copy
import json
from pathlib import Path

import pytest

from studio.agent_tools import validate_tool
from studio.child_context_projection import tool_argument_repairs
from studio.child_execution import recover_repeated_tool_error
from studio.errors import validation_summary
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore


def recorded():
    return json.loads((Path(__file__).parent / 'fixtures/recorded_repeated_binding_errors.json').read_text(encoding='utf-8'))


def record_actual_validation_failure(child, record):
    call = record['call']
    name, arguments = call['function']['name'], call['function']['arguments']
    records, memory, run = child.store.agents, child.runtime.memory, child.run
    records.begin_call(child.task['id'], run['id'], call['id'], name, arguments)
    with pytest.raises(ValueError) as failed:
        validate_tool(name, json.loads(arguments), child=True)
    output = {'ok': False, 'error': validation_summary(failed.value)}
    assert output == record['output']
    records.finish_call(run['id'], call['id'], output, run)
    context = memory.for_context(child.task['id'], child.agent['agent_id'], run['id'],
        call['id'], name, arguments, output, f"{run['id']}:{len(run['prepared'])}")
    return [{'role': 'assistant', 'content': '', 'tool_calls': [copy.deepcopy(call)]},
        {'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(context, ensure_ascii=False)}]


@pytest.mark.parametrize('delivered', [False, True])
def test_repeated_real_errors_archive_only_observed_history_and_preserve_the_latest_full_exchange(
        tmp_path, native_drawing_child, delivered):
    async def check():
        fixture = recorded()
        store = StudioStore(tmp_path / 'errors.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            made = child.call('draw_paths', {'paths': [
                {'name': '保留的既有轮廓', 'points': [[0, 0], [40, 20], [80, 0]]}]}, 'existing-native-contour')
            assert made['ok']
            first = record_actual_validation_failure(child, fixture['failures'][0])
            if delivered:
                runtime.memory.delivered(child.run['id'], first)
            latest = record_actual_validation_failure(child, fixture['failures'][1])
            history = [*first, *latest]
            before = copy.deepcopy((history, child.run))
            changes = store.connection.total_changes
            projected = recover_repeated_tool_error(runtime.memory, child.run, history)
            assert store.connection.total_changes == changes
            assert (history, child.run) == before
            assert projected[-2:] == latest
            assert recover_repeated_tool_error(runtime.memory, child.run, projected) == projected
            latest_row = store.connection.execute('SELECT observed_at FROM agent_memories WHERE caller_id=? AND call_id=?',
                (child.run['id'], fixture['failures'][1]['call']['id'])).fetchone()
            assert latest_row['observed_at'] is None
            if delivered:
                checkpoint = json.loads(projected[0]['content'])['repair_checkpoint']
                assert checkpoint['archived_count'] == 1
                assert checkpoint['recovery_for_call_id'] == fixture['failures'][1]['call']['id']
                assert not any(item.get('tool_call_id') == fixture['failures'][0]['call']['id'] for item in projected)
            else:
                assert projected == history
            for item in fixture['failures'][:2]:
                row = store.connection.execute('SELECT arguments,output FROM agent_tool_calls WHERE caller_id=? AND call_id=?',
                    (child.run['id'], item['call']['id'])).fetchone()
                assert row['arguments'] == item['call']['function']['arguments']
                assert json.loads(row['output']) == item['output']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


@pytest.mark.parametrize('latest_foreign', [False, True])
def test_error_history_from_another_agent_and_task_is_not_used(tmp_path, native_drawing_child, latest_foreign):
    async def check():
        fixture = recorded()
        store = StudioStore(tmp_path / 'owners.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        other_session = store.create_session()
        other_task = store.create_task(other_session['id'])
        other = native_drawing_child(store, runtime, other_session, other_task)
        try:
            first_child, latest_child = (child, other) if latest_foreign else (other, child)
            first = record_actual_validation_failure(first_child, fixture['failures'][0])
            runtime.memory.delivered(first_child.run['id'], first)
            latest = record_actual_validation_failure(latest_child, fixture['failures'][1])
            history = [*first, *latest]
            assert recover_repeated_tool_error(runtime.memory, child.run, history) == history
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_actual_rejected_canvas_call_has_a_valid_syntax_hint_without_execution():
    fixture = recorded()
    for record in fixture['failures']:
        call = record['call']
        messages = [{'role': 'assistant', 'content': '', 'tool_calls': [call]},
            {'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(record['output'])}]
        before = copy.deepcopy(messages)
        hint = tool_argument_repairs(messages)
        assert messages == before and len(hint) == 1
        assert hint[0]['call_id'] == call['id']
        assert {'reference_view_id', 'reference_layout_id'} <= set(hint[0]['omit_unused_optional_fields'])
        arguments = json.loads(call['function']['arguments'])
        proposed = {key: value for key, value in arguments.items() if key not in hint[0]['omit_unused_optional_fields']}
        assert proposed['observation_id'] == arguments['observation_id']
        assert proposed['coordinate_space'] == arguments['coordinate_space'] == 'image_normalized'
        assert proposed['paths'] == arguments['paths']
        assert validate_tool(call['function']['name'], proposed, child=True)
    successful = fixture['success']
    call = successful['call']
    assert validate_tool(call['function']['name'], json.loads(call['function']['arguments']), child=True)
    assert tool_argument_repairs([{'role': 'assistant', 'content': '', 'tool_calls': [call]},
        {'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(successful['output'])}]) == []

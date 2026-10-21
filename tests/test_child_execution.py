import asyncio
import copy
import json
import time
from pathlib import Path

import pytest

from studio.child_execution import checkpoint_reason, continue_repair, current_feedback, execution_context, verification_checkpoint
from studio.child_drawing_memory import drawing_memory_context
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore
from studio.vision import scene_digest


def test_review_schedule_uses_candidate_and_stagnation_checkpoints():
    assert checkpoint_reason(None) is None
    assert checkpoint_reason({'first_changed_draft': True}) is None
    assert checkpoint_reason({'changed_after_review': 'previous'}) is None
    for reason in ({'completion_candidate': True}, {'repeated_read': {'query': 'current'}},
                   {'retry_review_error': 'previous'}, {'new_reason': True},
                   {'first_changed_draft': True, 'completion_candidate': True}):
        assert checkpoint_reason(reason) == reason


def test_recorded_reports_keep_exact_live_form_bindings_and_full_errors():
    recorded = json.loads((Path(__file__).parent / 'fixtures/recorded_report_failures.json').read_text(encoding='utf-8'))
    report = json.loads(recorded['reports'][-1]['response']['message']['content'])
    native = recorded['native_elements']
    removed = report['bindings'][0]['object_ids'][0]
    run = {'draft': [item for item in native if item['id'] != removed]}
    previous = {**report, 'composition_id': 'recorded-composition'}
    error = recorded['reports'][0]['error'] if 'error' in recorded['reports'][0] else recorded['reports'][0]
    messages = [{'role': 'system', 'content': 'Current child role'},
        {'role': 'user', 'content': json.dumps({'goal': recorded['assignment'], 'previous_drawspec_report': previous})},
        {'role': 'user', 'content': json.dumps(error)}]
    before = copy.deepcopy(messages)
    result = execution_context(run, messages)
    prompt = json.loads(result[1]['content'])
    assert messages == before
    assert result[0] == messages[0] and result[2:] == messages[2:]
    assert prompt['goal'] == recorded['assignment']
    live = {item['id'] for item in run['draft'] if not item.get('isDeleted')}
    assert prompt['inherited_form_bindings']['bindings'] == [
        {'form_id': binding['form_id'], 'object_ids': [key for key in binding['object_ids'] if key in live]}
        for binding in report['bindings'] if set(binding['object_ids']) & live]
    assert 'previous_drawspec_report' not in prompt


def test_current_native_index_and_curve_method_follow_real_changes(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'execution.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            made = child.call('draw_paths', {'paths': [
                {'name': '建筑平面', 'closed': True, 'smooth': False, 'points': [[0, 0], [90, 0], [90, 100], [0, 100]]},
                {'name': '曲线', 'points': [[100, 0], [150, 50], [100, 100]]}]}, 'actual-methods')
            assert made['ok']
            before = store.connection.total_changes
            journal = json.loads(drawing_memory_context(runtime.memory, child.run, [])['content'])['drawing_execution_memory']
            assert store.connection.total_changes == before
            assert journal['current_object_index']['total_count'] == 2
            assert [item['id'] for item in journal['current_object_index']['objects']] == made['changed_ids']
            assert [item['smooth'] for item in journal['recent_actual_actions'][0]['stroke_method']['examples']] == [False, True]
            assert current_feedback(store, child.run) is None
            moved = child.call('move_elements', {'ids': made['changed_ids'], 'dx': 12, 'dy': 82}, 'move-native')
            assert moved['ok']
            child.call('compact_context', {'goal': child.run['message'], 'summary': '继续当前构造步骤',
                'constraints': [f"current_input_tag=drawing_input={child.run['input_id']}"],
                'object_ids': made['changed_ids']}, 'save-owned-step')
            notes = runtime.memory.snapshot(task['id'], child.run['agent_id'], child.run['id'],
                f"{child.run['id']}:{len(child.run['prepared'])}", child.document)
            journal = json.loads(drawing_memory_context(runtime.memory, child.run,
                [{'role': 'user', 'content': json.dumps(notes)}])['content'])['drawing_execution_memory']
            assert not journal['saved_step']['tag_matches_current_input']
            assert journal['saved_step']['created_in_current_input']
            move = journal['recent_actual_actions'][-1]
            assert move['executed_displacement'] == {'dx': 12, 'dy': 82}
            assert move['current_bounds_after_all_actions'][0]['bounds'] == [12, 82, 90, 100]
            assert journal['current_object_index']['objects'][0]['bounds'] == [12, 82, 90, 100]
            child.call('delete_elements', {'ids': [made['changed_ids'][0]]}, 'remove-plane')
            journal = json.loads(drawing_memory_context(runtime.memory, child.run, [])['content'])['drawing_execution_memory']
            assert [item['id'] for item in journal['current_object_index']['objects']] == made['changed_ids'][1:]
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_recorded_repair_stays_in_input_and_other_review_states_hand_back():
    recorded = json.loads((Path(__file__).parent / 'fixtures/recorded_ineffective_draft.json').read_text(encoding='utf-8'))
    assert continue_repair({'completion': {'review_status': recorded['review']['status']}})
    for status in ('ready', 'blocked', 'error'):
        assert not continue_repair({'completion': {'review_status': status}})


@pytest.mark.parametrize('operation', ['erase_strokes', 'delete_elements'])
def test_explicit_full_view_can_verify_a_real_erasure_correction(tmp_path, native_drawing_child, operation):
    async def check():
        store = StudioStore(tmp_path / 'erasure-verification.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            made = child.call('draw_paths', {'paths': [
                {'name': '当前轮廓', 'points': [[0, 0], [100, 0]], 'smooth': False},
                {'name': '保留轮廓', 'points': [[0, 80], [100, 80]], 'smooth': False}]}, 'actual-lines')
            assert made['ok']
            # 真实 repair 记录只用于复核调度，不评价这些测试笔画的美术质量。
            recorded = json.loads((Path(__file__).parent / 'fixtures/recorded_ineffective_draft.json').read_text(encoding='utf-8'))
            created = time.time()
            with store.lock, store.connection:
                store.connection.execute('INSERT INTO draft_reviews VALUES(?,?,?,?,?,?,?)', (
                    'recorded-repair', task['id'], child.run['id'], scene_digest(child.document),
                    json.dumps(recorded['review']), created, created))
            arguments = {'ids': [made['changed_ids'][0]]}
            if operation == 'erase_strokes':
                arguments.update(points=[[50, 0]], radius=8)
            changed = child.call(operation, arguments, 'actual-correction')
            assert changed['ok'] and changed['action_count'] > 0
            records = store.agents
            assert records.begin_call(task['id'], child.run['id'], 'verify-current', 'view_canvas', '{}') is None
            observed = await runtime.vision.observe(task['id'], child.run['agent_id'], child.run['id'],
                child.document, None, remember_region=True)
            records.finish_call(child.run['id'], 'verify-current', observed, child.run)
            checkpoint = verification_checkpoint(store, child.run, {'changed_after_review': 'recorded-repair'})
            assert checkpoint == ({'repair_verification': {'review_key': 'recorded-repair',
                'call_id': 'verify-current'}} if operation == 'erase_strokes' else None)
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())

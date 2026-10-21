import asyncio
import copy
import json
from pathlib import Path

import pytest

from studio.agents import DrawingAgents
from studio.child_drawing_memory import drawing_memory_context, stroke_method
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def context(runtime, child):
    model = DrawingAgents(runtime.store, ModelConfig(provider='ollama', model='llama3.1',
        api_base='http://localhost:11434'))
    messages = runtime.memory.prepare_messages(child.task['id'], child.agent['agent_id'], child.run['id'],
        f"{child.run['id']}:{len(child.run['prepared'])}", child.document,
        model.child_messages(child.run, runtime.store.agents.get_agent(child.session['id'], child.agent['agent_id'])))
    return messages, json.loads(drawing_memory_context(runtime.memory, child.run, messages)['content'])['drawing_execution_memory']


def test_actual_native_strokes_and_saved_steps_survive_restart_without_writes(tmp_path, native_drawing_child):
    async def check():
        path = tmp_path / 'steps.sqlite3'
        store = StudioStore(path)
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        output = child.call('draw_paths', {'paths': [
            {'name': '连续笔画', 'points': [[0, 0], [50, 150], [0, 300]]},
            {'name': '独立笔画', 'points': [[80, 0], [90, 15], [80, 30]]}]}, 'strokes')
        assert output['ok'] and len(output['changed_ids']) == 2
        notes = {'expected_revision': 0, 'goal': child.run['message'],
            'summary': '目标：连接左侧形体；方法：修改既有轮廓；保护：保留右侧笔画。',
            'constraints': [f"drawing_input={child.run['input_id']}"],
            'completed': ['两条原生笔画已经存在，视觉结构等待检查'], 'pending': ['检查连接'],
            'object_ids': output['changed_ids']}
        saved = child.call('compact_context', notes, 'save-step')
        assert saved['ok'] and saved['revision'] == 1
        before_changes = store.connection.total_changes
        before_run = copy.deepcopy(store.agents.get_run(session['id'], child.run['id']))
        messages, journal = context(runtime, child)
        assert store.connection.total_changes == before_changes
        assert store.agents.get_run(session['id'], child.run['id']) == before_run
        assert journal['saved_step']['tag_matches_current_input']
        assert journal['saved_step']['memory_ref'] == saved['memory_ref']
        action = journal['recent_actual_actions'][0]
        assert action['current_object_ids'] == output['changed_ids']
        assert action['stroke_method']['separate_strokes'] == 2
        assert action['stroke_method']['examples'][0]['span'] == [50, 300]
        assert 'points' not in action['stroke_method']['examples'][0]
        run = copy.deepcopy(child.run)
        await runtime.close()
        store.close()
        reopened = StudioStore(path)
        continued = DrawingRuntime(reopened, ModelSettings(reopened))
        try:
            recovered = json.loads(drawing_memory_context(continued.memory, run, messages)['content'])['drawing_execution_memory']
            assert recovered == journal
            document = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
            exact = continued.memory.recall(task['id'], run['agent_id'],
                {'element_id': output['changed_ids'][0], 'point_tolerance': 0, 'point_limit': 256}, document)
            assert exact['geometry_exact'] and exact['source_point_count'] > 3
        finally:
            await continued.close()
            reopened.close()
    asyncio.run(check())


def test_journal_separates_agents_canvases_and_queued_inputs(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'scopes.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        first = native_drawing_child(store, runtime, session, task)
        other_agent = store.agents.create_agent(session['id'], task['id'], 'other-child',
            '准备独立原生对象', 'Other', [])
        other = native_drawing_child(store, runtime, session, task, other_agent)
        isolated_session = store.create_session()
        isolated_task = store.create_task(isolated_session['id'])
        isolated = native_drawing_child(store, runtime, isolated_session, isolated_task)
        try:
            own = first.call('create_element', {'type': 'ellipse'}, 'own')
            foreign = other.call('create_element', {'type': 'rectangle'}, 'foreign')
            isolated.call('create_element', {'type': 'diamond'}, 'isolated')
            first.call('compact_context', {'goal': first.run['message'], 'summary': '当前输入的步骤',
                'constraints': [f"drawing_input={first.run['input_id']}"], 'object_ids': own['changed_ids']}, 'notes')
            _, journal = context(runtime, first)
            assert len(journal['recent_actual_actions']) == 1
            assert journal['recent_actual_actions'][0]['current_object_ids'] == own['changed_ids']
            assert not set(foreign['changed_ids']) & set(journal['recent_actual_actions'][0]['current_object_ids'])
            with pytest.raises(ValueError, match='exact current child input'):
                drawing_memory_context(runtime.memory, {**first.run, 'agent_id': other.run['agent_id']}, [])
            store.agents.complete_run(session['id'], first.run['id'], '交接当前原生候选')
            store.agents.enqueue_input(session['id'], task['id'], first.agent['agent_id'],
                'next-owned-input', '继续修改原生对象', [])
            first.run = runtime.children._initialize(session['id'],
                store.agents.next_run(session['id'], first.agent['agent_id'], task['id']))
            _, next_journal = context(runtime, first)
            assert next_journal['recent_actual_actions'] == []
            assert not next_journal['saved_step']['tag_matches_current_input']
            assert next_journal['saved_step']['revision'] == 1
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_deleted_ids_and_all_tool_errors_remain_explicit(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'errors.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            created = child.call('create_element', {'type': 'ellipse'}, 'created')
            removed = child.call('delete_elements', {'ids': created['changed_ids']}, 'removed')
            assert removed['ok']
            error = child.call('move_elements', {'ids': created['changed_ids'], 'dx': 20, 'dy': 0}, 'missing')
            assert error['ok'] is False
            messages, journal = context(runtime, child)
            errors = [json.loads(item['content']) for item in messages if item.get('role') == 'tool']
            assert error in errors
            assert all(not item['current_object_ids'] for item in journal['recent_actual_actions'])
            assert journal['recent_actual_actions'][-1]['removed_object_ids'] == created['changed_ids']
            after = drawing_memory_context(runtime.memory, child.run, messages)
            assert after['role'] == 'user'
            assert error in [json.loads(item['content']) for item in messages if item.get('role') == 'tool']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_only_delivered_recorded_reviews_enter_this_inputs_history(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'reviews.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        other_agent = store.agents.create_agent(session['id'], task['id'], 'other-review-child',
            '检查独立任务的历史', 'Other', [])
        other = native_drawing_child(store, runtime, session, task, other_agent)
        recorded = json.loads((Path(__file__).parent / 'fixtures/draft-review-replan-response.json').read_text(encoding='utf-8'))['result']
        try:
            # 评审原文只用于验证历史传递，不将其作为测试画面的质量判定。
            with store.lock, store.connection:
                for index, (run_id, delivered) in enumerate(((child.run['id'], 1), (child.run['id'], None), (other.run['id'], 1))):
                    receipt = {**recorded, 'review_key': f"{recorded['review_key']}-{index}"}
                    store.connection.execute('INSERT INTO draft_reviews VALUES(?,?,?,?,?,?,?)',
                        (receipt['review_key'], task['id'], run_id, receipt['scene_hash'], json.dumps(receipt), index + 1, delivered))
            _, journal = context(runtime, child)
            assert [item['review_key'] for item in journal['delivered_comparisons']] == [recorded['review_key'] + '-0']
            assert journal['delivered_comparisons'][0]['observed_result'] == recorded['assessment']['revision']['evidence'][:600]
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_stroke_summary_distinguishes_image_units_and_separate_pen_lifts():
    method = stroke_method({'coordinate_space': 'image_normalized', 'observation_id': 'current-image',
        'opacity': 18, 'paths': [{'name': '笔画', 'points': [[110, 170], [155, 225], [165, 940]]}]})
    assert method['separate_strokes'] == 1
    assert method['coordinate_space'] == 'image_normalized'
    assert method['examples'][0]['span'] == [55, 770]
    assert method['examples'][0]['opacity'] == 18


def test_current_step_rebuilds_from_real_cleanup_and_preserves_inherited_style(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'construction.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            created = child.call('draw_paths', {'opacity': 23, 'strokeWidth': 0.6, 'backgroundColor': '#ffffff',
                'paths': [{'name': '前景形体', 'closed': True, 'points': [[0, 0], [50, 0], [25, 50]]}]}, 'filled')
            target = created['changed_ids'][0]
            changed = child.call('reshape_path', {'element_id': target,
                'points': [[0, 0], [60, 0], [30, 60]]}, 'reshaped')
            assert changed['ok']
            messages, journal = context(runtime, child)
            inherited = journal['recent_actual_actions'][-1]['stroke_method']['examples'][0]
            assert all(inherited[key] == 'inherit_current_object'
                for key in ('closed', 'backgroundColor', 'strokeWidth', 'opacity'))
            element = next(item for item in child.run['draft'] if item['id'] == target)
            assert (element['backgroundColor'], element['opacity'], element['strokeWidth']) == ('#ffffff', 23, 0.6)
            removed = child.call('erase_strokes', {'ids': [target], 'mode': 'stroke',
                'points': [[30, 30]], 'radius': 80}, 'cleanup')
            assert removed['ok'] and removed['action_count']
            messages, journal = context(runtime, child)
            assert journal['work_phase']['cleanup_was_last_operation']
            assert journal['recent_actual_actions'][-1]['removed_object_ids'] == [target]
            assert 'erasure-only' in journal['work_phase']['instruction']
            assert journal['next_step_focus'] is None
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_filled_object_memory_tracks_actual_reordering_and_removal(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'layers.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            head = child.call('create_element', {'type': 'ellipse', 'x': 20, 'y': 20,
                'width': 60, 'height': 60, 'backgroundColor': '#ef4444', 'name': '前景形体'}, 'head')['changed_ids'][0]
            body = child.call('create_element', {'type': 'ellipse', 'x': 0, 'y': 0,
                'width': 140, 'height': 140, 'backgroundColor': '#ffffff', 'name': '后方形体'}, 'body')['changed_ids'][0]
            _, before = context(runtime, child)
            objects = before['current_filled_objects']['objects']
            assert [item['id'] for item in objects] == [head, body]
            assert objects[0]['native_bounds'] == [20, 20, 60, 60]
            assert objects[1]['backgroundColor'] == '#ffffff'
            changed = child.call('reorder_elements', {'ids': [head], 'position': 'front'}, 'foreground')
            assert changed['ok'] and changed['action_count'] == 1
            _, after = context(runtime, child)
            assert [item['id'] for item in after['current_filled_objects']['objects']] == [body, head]
            assert after['current_filled_objects']['objects'][-1]['position_in_current_draft'] == 1
            child.call('delete_elements', {'ids': [body]}, 'remove-body')
            _, cleaned = context(runtime, child)
            assert cleaned['current_filled_objects']['total_count'] == 1
            assert [item['id'] for item in cleaned['current_filled_objects']['objects']] == [head]
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())

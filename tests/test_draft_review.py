import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from studio.agent_tools import validate_tool
from studio.actions import element_fingerprint
from studio.draft_review import DraftAssessment, DraftRegion, parse_draft_assessment
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def test_child_handoff_requires_unresolved_work_and_main_completion_remains_whole_request():
    assert validate_tool('finish_drawing', {'summary': '轮廓已准备'}, child=True).get('completion_status', 'ready') == 'ready'
    assert validate_tool('finish_drawing', {'summary': '等待主 Agent 调整区域', 'completion_status': 'blocked',
        'unresolved': ['原区域与要求保留的对象重叠']}, child=True)['completion_status'] == 'blocked'
    for args in [
        {'summary': '尚有阻碍', 'completion_status': 'blocked'},
        {'summary': '尚有阻碍', 'completion_status': 'blocked', 'unresolved': [' ']},
        {'summary': '尚有阻碍', 'completion_status': 'ready', 'unresolved': ['结构待修改']},
    ]:
        with pytest.raises(ValueError):
            validate_tool('finish_drawing', args, child=True)
    with pytest.raises(ValueError):
        validate_tool('finish_drawing', {'summary': '尚有阻碍', 'completion_status': 'blocked', 'unresolved': ['需要修改']})


def test_planning_without_prepared_actions_does_not_masquerade_as_a_completed_visual_review(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'planning.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        config = ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434')
        try:
            child = native_drawing_child(store, runtime, session, task)
            assert await runtime.children.draft_reviews.review(child.run, config) is None
            assert await runtime.children.draft_reviews.review(child.run, config, {'repeated_read': {'tool_name': 'inspect_scene'}}) is None
            completion = await runtime.children.draft_reviews.review(child.run, config, {'completion_candidate': True})
            assert completion['ok'] and completion['review_key'] is None
            assert store.connection.execute('SELECT count(*) FROM draft_reviews').fetchone()[0] == 0
            assert store.connection.execute('SELECT count(*) FROM tool_logs WHERE kind=?', ('draft_review_context',)).fetchone()[0] == 0
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_draft_baseline_includes_inherited_pending_work_and_blocked_handoff_is_durable(tmp_path, native_drawing_child):
    async def check():
        path = tmp_path / 'baseline.sqlite3'
        store = StudioStore(path)
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        child.call('create_element', {'type': 'ellipse', 'name': '未提交轮廓'}, 'outline')
        completion = {'status': 'blocked', 'unresolved': ['需要主 Agent 确认新的目标区域'], 'review_status': 'blocked'}
        result = store.agents.complete_run(session['id'], child.run['id'], '未完成，等待调整输入', completion=completion)
        continued = native_drawing_child(store, runtime, session, task, child.agent)
        assert continued.run['document']['depends_on'] == [result['result_id']]
        before = json.loads(store.connection.execute('SELECT document FROM draft_review_baselines WHERE run_id=?',
            (continued.run['id'],)).fetchone()[0])
        assert [item['id'] for item in before['elements']] == [item['id'] for item in child.run['draft']]
        assert element_fingerprint(before['elements'][0]) == element_fingerprint(child.run['draft'][0])
        continued.call('move_elements', {'ids': [child.run['draft'][0]['id']], 'dx': 17, 'dy': 23}, 'adjust')
        runtime.children.draft_reviews.begin(continued.run)
        assert json.loads(store.connection.execute('SELECT document FROM draft_review_baselines WHERE run_id=?',
            (continued.run['id'],)).fetchone()[0]) == before
        assert store.get_document(session['document']['id'])['elements'] == []
        await runtime.close()
        store.close()
        store = StudioStore(path)
        runtime = DrawingRuntime(store, ModelSettings(store))
        try:
            assert store.agents.run_summary(store.agents.get_run(session['id'], child.run['id']))['completion'] == completion
            assert store.agents.get_agent(session['id'], child.agent['agent_id'])['latest_result']['completion'] == completion
            assert store.agents.get_run(session['id'], continued.run['id'])['prepared'] == continued.run['prepared']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_review_identity_tracks_draft_input_and_configuration_and_reuses_unchanged_focus_with_private_scope(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'identity.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        config = ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434')
        try:
            child = native_drawing_child(store, runtime, session, task)
            element_id = child.call('create_element', {'type': 'ellipse'}, 'circle')['changed_ids'][0]
            reviews = runtime.children.draft_reviews
            first = (await reviews._inputs(child.run, config))[-1]
            assert (await reviews._inputs(child.run, config))[-1] == first
            assert (await reviews._inputs(child.run, config.model_copy(update={'api_key': 'changed-secret'})))[-1] == first
            assert (await reviews._inputs(child.run, config.model_copy(update={'model': 'other-model'})))[-1] != first
            assert (await reviews._inputs({**child.run, 'message': '另一条输入'}, config))[-1] != first
            assert child.run['selection'] == [element_id]
            assert (await reviews._inputs({**child.run, 'selection': []}, config))[-1] != first
            await runtime.vision.observe(task['id'], child.agent['agent_id'], child.run['id'], child.document,
                {'x': 0, 'y': 0, 'width': 50, 'height': 50}, remember_region=True)
            focused = (await reviews._inputs(child.run, config))[-1]
            assert focused == first
            child.call('move_elements', {'ids': [element_id], 'dx': 17, 'dy': 23}, 'move')
            assert (await reviews._inputs(child.run, config))[-1] != focused
            other = native_drawing_child(store, runtime, session, task)
            assert not reviews.observed(other.run['id'], first)
            assert store.get_document(session['document']['id'])['elements'] == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_recorded_draft_review_requires_current_object_ids_and_concrete_finding():
    fixture = json.loads((Path(__file__).parent / 'fixtures/draft-review-repair-response.json').read_text(encoding='utf-8'))
    raw = fixture['response']['tool_calls'][0]['function']['arguments']
    assert hashlib.sha256(raw.encode()).hexdigest() == fixture['argument_sha256']
    document = {'elements': [{'id': item} for item in fixture['current_object_ids']]}
    assessment, failure = parse_draft_assessment(fixture['response'], document)
    assert failure is None and assessment.status == 'repair' and assessment.findings
    assert all(item.defect and item.repair for item in assessment.findings)
    assessment, failure = parse_draft_assessment(fixture['response'], {'elements': []})
    assert assessment is None and failure['review_response'] == fixture['response']
    assert 'exist in the current private draft' in failure['error']
    with pytest.raises(ValueError, match='no blocking finding'):
        DraftAssessment.model_validate({**json.loads(raw), 'status': 'ready'})
    for area in [{'x': 900, 'y': 0, 'width': 200, 'height': 100},
                 {'x': 0, 'y': 0, 'width': float('inf'), 'height': 100}]:
        with pytest.raises(ValueError):
            DraftRegion.model_validate(area)


def test_compacted_conversation_and_actual_pending_response_survive_shutdown_together(tmp_path, native_drawing_child):
    async def check():
        path = tmp_path / 'pending.sqlite3'
        store = StudioStore(path)
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        child.call('create_element', {'type': 'ellipse'}, 'outline')
        child.run['conversation'] = [{'role': 'user', 'content': json.dumps({
            'read_checkpoint': {'caller_id': child.run['id'], 'archived_count': 2}})}]
        fixture = json.loads((Path(__file__).parent / 'fixtures/draft-review-repair-response.json').read_text(encoding='utf-8'))
        previous_messages = store.connection.execute('SELECT count(*) FROM agent_messages WHERE run_id=?',
            (child.run['id'],)).fetchone()[0]
        store.agents.save_response(child.run, fixture['response'])
        await runtime.close()
        store.close()
        store = StudioStore(path)
        runtime = DrawingRuntime(store, ModelSettings(store))
        try:
            recovered = store.agents.get_run(session['id'], child.run['id'])
            assert recovered['conversation'] == child.run['conversation']
            assert recovered['pending_response'] == fixture['response']
            assert recovered['prepared'] == child.run['prepared']
            assert store.connection.execute('SELECT count(*) FROM agent_messages WHERE run_id=?',
                (child.run['id'],)).fetchone()[0] == previous_messages + 1
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())

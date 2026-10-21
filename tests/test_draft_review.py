import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest

from studio.agent_tools import CHILD_TOOLS, MAIN_TOOLS, validate_tool
from studio.actions import element_fingerprint
from studio.child_execution import continue_repair
from studio.draft_review import DraftAssessment, DraftFinding, DraftRegion, DraftRevision, parse_draft_assessment
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore
from studio.vision import scene_digest


@pytest.mark.parametrize('child,tools', [(False, MAIN_TOOLS), (True, CHILD_TOOLS)])
def test_drawing_candidates_use_natural_completion_instead_of_a_completion_tool(child, tools):
    assert 'finish_drawing' not in {item['function']['name'] for item in tools}
    with pytest.raises(ValueError):
        validate_tool('finish_drawing', {'summary': '完成'}, child=child)


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


def test_draft_finding_normalizes_object_id_spelling_without_weakening_validation():
    values = {'defect': '背景线穿过主体', 'repair': '修复前后遮挡关系'}
    for key in ('object_ids', 'objectIds'):
        finding = DraftFinding.model_validate({**values, key: ['current-object']})
        assert finding.model_dump() == {**values, 'object_ids': ['current-object'], 'draft_region': None}
    assert 'objectIds' not in DraftFinding.model_json_schema()['properties']
    for invalid in [{'object_ids': ['one'], 'objectIds': ['two']},
                    {'objectIds': 'one'}, {'objectIDs': ['one']}]:
        with pytest.raises(ValueError):
            DraftFinding.model_validate({**values, **invalid})
    with pytest.raises(ValueError, match='no blocking finding'):
        DraftAssessment.model_validate({'status': 'ready', 'visible_state': '主体仍有遮挡问题',
            'findings': [{**values, 'objectIds': ['current-object']}], 'next_action': '修复遮挡'})


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


def test_recorded_ineffective_repair_requires_a_real_comparison_and_a_new_strategy():
    fixture = json.loads((Path(__file__).parent / 'fixtures/draft-review-replan-response.json').read_text(encoding='utf-8'))
    message = fixture['response']
    assert hashlib.sha256(message['tool_calls'][0]['function']['arguments'].encode()).hexdigest() == fixture['argument_sha256']
    document = {'elements': [{'id': item} for item in fixture['current_object_ids']]}
    assessment, failure = parse_draft_assessment(message, document, compared=True)
    assert failure is None and assessment.status == 'repair'
    assert assessment.revision.change == 'ineffective' and assessment.revision.next_strategy
    assessment, failure = parse_draft_assessment(message, document)
    assert assessment is None and 'actual previous reviewed draft' in failure['error']
    with pytest.raises(ValueError, match='different next strategy'):
        DraftRevision.model_validate({'change': 'ineffective', 'evidence': '相同结构缺陷仍可见'})
    ordinary = json.loads((Path(__file__).parent / 'fixtures/draft-review-repair-response.json').read_text(encoding='utf-8'))
    _, failure = parse_draft_assessment(ordinary['response'], {'elements': [
        {'id': item} for item in ordinary['current_object_ids']]}, compared=True)
    assert 'report revision' in failure['error']


def test_replan_uses_saved_current_run_feedback_and_waits_for_full_tool_error_delivery(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'route.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        original = child.call('create_element', {'type': 'ellipse'}, 'outline')
        reviews = runtime.children.draft_reviews
        try:
            # 仅重放真实反馈检查路由和归属，不评价此测试画布的画质。
            fixture = json.loads((Path(__file__).parent / 'fixtures/draft-review-replan-response.json').read_text(encoding='utf-8'))
            result = {**fixture['result'], 'scene_hash': scene_digest(child.document), 'selection': child.run['selection']}
            assert reviews.replan_completion(child.run, result) is None
            with store.lock, store.connection:
                store.connection.execute('INSERT INTO draft_reviews VALUES(?,?,?,?,?,?,NULL)', (
                    result['review_key'], task['id'], child.run['id'], result['scene_hash'], json.dumps(result), time.time()))
            completion = reviews.replan_completion(child.run, result)
            assert completion['status'] == 'blocked' and completion['reason'] == 'ineffective_repair'
            assert completion['next_strategy']
            assert not continue_repair({'completion': completion}, {'unresolved': [], 'checks': []})
            assert reviews.replan_completion({**child.run, 'id': 'foreign-run'}, result) is None
            assert reviews.replan_completion({**child.run, 'selection': []}, result) is None
            error = child.call('move_elements', {'ids': ['missing'], 'dx': 1, 'dy': 0}, 'bad-move')
            assert error['ok'] is False and reviews.replan_completion(child.run, result) is None
            pending = runtime.memory.prepare_messages(task['id'], child.agent['agent_id'], child.run['id'],
                'current-state', child.document, [])
            assert any(item.get('tool_call_id') == 'bad-move' and json.loads(item['content']) == error for item in pending)
            runtime.memory.delivered(child.run['id'], pending)
            assert reviews.replan_completion(child.run, result) == completion
            with store.lock, store.connection:
                store.connection.execute('UPDATE draft_reviews SET result=? WHERE review_key=?', (
                    json.dumps({**result, 'constructive_correction': False}), result['review_key']))
            assert reviews.replan_completion(child.run, result) is None
            with store.lock, store.connection:
                store.connection.execute('UPDATE draft_reviews SET result=? WHERE review_key=?', (json.dumps(result), result['review_key']))
            child.call('move_elements', {'ids': original['changed_ids'], 'dx': 17, 'dy': 23}, 'new-state')
            assert reviews.replan_completion(child.run, result) is None
            assert store.get_document(session['document']['id'])['elements'] == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_previous_draft_comparison_requires_this_run_delivered_pixels_and_an_actual_scene_change(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'comparison.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        original = child.call('create_element', {'type': 'ellipse'}, 'outline')
        reviews = runtime.children.draft_reviews
        try:
            image = await runtime.vision.observe(task['id'], child.agent['agent_id'], child.run['id'], child.document)
            fixture = json.loads((Path(__file__).parent / 'fixtures/draft-review-replan-response.json').read_text(encoding='utf-8'))
            result = {**fixture['result'], 'scene_hash': scene_digest(child.document), 'full_observation_id': image['observation_id']}
            with store.lock, store.connection:
                store.connection.execute('INSERT INTO draft_reviews VALUES(?,?,?,?,?,?,NULL)', (
                    result['review_key'], task['id'], child.run['id'], result['scene_hash'], json.dumps(result), time.time()))
            assert reviews.previous(child.run, result['scene_hash']) is None
            child.call('delete_elements', {'ids': original['changed_ids']}, 'cleanup')
            assert reviews.previous(child.run, scene_digest(child.document)) is None
            reviews.delivered(child.run['id'], result['review_key'])
            previous = reviews.previous(child.run, scene_digest(child.document))
            assert previous['image_metadata']['observation_id'] == image['observation_id']
            assert previous['changes'][0]['tool'] == 'delete_elements'
            assert previous['assessment'] == fixture['result']['assessment']
            assert reviews.previous({**child.run, 'id': 'foreign-run'}, scene_digest(child.document)) is None
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())

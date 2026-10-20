import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest

from studio.actions import apply_action, create_native_element
from studio.quality import DrawingQuality, assessment_messages, drawing_change_summary, parse_assessment_response, review_scene_key
from studio.settings import ModelConfig, ModelSettings
from studio.runtime import DrawingRuntime
from studio.store import StudioStore
from studio.vision import CanvasVision


def document():
    elements = [create_native_element({'type': 'ellipse', 'name': '红色圆', 'x': 20, 'y': 30,
        'width': 40, 'height': 40, 'strokeColor': '#ef4444'}),
        create_native_element({'type': 'rectangle', 'name': '蓝色方块', 'x': 200, 'y': 60})]
    return {'id': 'canvas', 'elements': elements, 'selection': [elements[0]['id']]}


def test_review_change_summary_distinguishes_original_work_and_precise_local_edit():
    before = document()
    moved, selection, _ = apply_action(before['elements'], before['selection'], {
        'kind': 'move_elements', 'args': {'ids': [before['elements'][0]['id']], 'dx': 17, 'dy': 23}})
    after = {**before, 'elements': moved, 'selection': selection}
    summary = drawing_change_summary(before, after)
    assert summary['modified_count'] == 1 and summary['added_count'] == summary['removed_count'] == 0
    sample = summary['sampled_changes'][0]
    assert sample['before']['x'] == 20 and sample['after']['x'] == 37
    assert sample['before']['y'] == 30 and sample['after']['y'] == 53
    assert drawing_change_summary({**before, 'elements': []}, before)['added_count'] == 2
    assert drawing_change_summary(before, {**before, 'elements': []})['removed_count'] == 2


def test_review_content_identity_includes_selection_and_visible_seed_but_ignores_editor_metadata():
    initial = document()
    normalized = copy.deepcopy(initial)
    normalized['elements'][0].update(version=99, versionNonce=123, updated=567, index='a0')
    assert review_scene_key(initial) == review_scene_key(normalized)
    normalized['elements'][0]['seed'] += 1
    assert review_scene_key(initial) != review_scene_key(normalized)
    normalized = {**initial, 'selection': []}
    assert review_scene_key(initial) != review_scene_key(normalized)


def test_baseline_is_task_scoped_durable_and_does_not_follow_later_edits(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'baseline.sqlite3')
        vision = CanvasVision(store)
        quality = DrawingQuality(store, vision)
        try:
            first = store.create_session()
            other = store.create_session()
            task = store.create_task(first['id'])
            with pytest.raises(ValueError, match='another session'):
                quality.begin(task['id'], other['document'], '画一个圆')
            quality.begin(task['id'], first['document'], '画一个圆')
            changed = store.commit_action(first['document']['id'], {'kind': 'create_element', 'args': {
                'type': 'ellipse'}}, {})['document']
            quality.begin(task['id'], changed, 'a later invocation cannot replace the initial request')
            assert quality.baseline(task['id']) == {'request': '画一个圆', 'document': first['document']}
            assert store.get_document(first['document']['id']) == changed
            assert len(store.get_history(first['document']['id'], 'undo')) == 1
            next_task = store.create_task(first['id'])
            with pytest.raises(ValueError, match='no saved initial canvas'):
                quality.baseline(next_task['id'])
        finally:
            await vision.close()
            store.close()

    asyncio.run(check())


@pytest.mark.parametrize('kind', ['malformed', 'local-edit', 'unknown-field'])
def test_actual_recorded_reviewer_responses_cannot_turn_invalid_json_into_approval(kind):
    fixture = json.loads((Path(__file__).parent / 'fixtures' / f'quality-{kind}-response.json').read_text(encoding='utf-8'))
    raw = fixture['response']['tool_calls'][0]['function']['arguments']
    assert hashlib.sha256(raw.encode()).hexdigest() == fixture['argument_sha256']
    assessment, failure = parse_assessment_response(fixture['response'])
    if kind != 'local-edit':
        assert assessment is None and failure['ok'] is False
        assert failure['review_response'] == fixture['response'] and 'invalid assessment' in failure['error']
    else:
        assert failure is None and assessment.goal_satisfied
        assert all(not criterion.applicable or criterion.passed for criterion in assessment.criteria)


def test_reviewer_receives_its_own_full_schema_error_only_for_the_same_task_pixels_and_configuration(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'reviewer-feedback.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        config = ModelConfig(provider='ark', model='ark-code-latest', api_base='https://example.org/model')
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            initial = session['document']
            runtime.quality.begin(task['id'], initial, '画一个圆')
            key, cached = runtime.quality.current_review(task['id'], config, initial)
            assert cached is None
            fixture = json.loads((Path(__file__).parent / 'fixtures/quality-unknown-field-response.json').read_text(encoding='utf-8'))
            assessment, failure = parse_assessment_response(fixture['response'])
            assert assessment is None and failure['error_kind'] == 'assessment_format'
            payload = {'review_key': key, 'scene_key': review_scene_key(initial), 'result': failure,
                       'blind_description': 'Recorded description for this test transport; not a visual approval.'}
            store.log_tool(task['id'], 'quality', 0, 'quality_schema_error', payload)
            feedback = runtime.quality.schema_feedback(task['id'], key)
            assert feedback == payload
            messages = assessment_messages([], feedback)
            assert json.loads(messages[-1]['content'])['assessment_format_repair'] == failure
            assert failure['review_response'] == fixture['response']
            assert runtime.quality.current_review(task['id'], config, initial)[1] is None
            main_feedback, keys = runtime.quality.context(task['id'], config, initial)
            assert keys == [key]
            assert json.loads(main_feedback[-1]['content'])['current_drawing_review_error'] == failure
            assert len(assessment_messages([])) == 2
            changed_key, _ = runtime.quality.current_review(task['id'], config.model_copy(update={'model': 'another-model'}), initial)
            assert runtime.quality.schema_feedback(task['id'], changed_key) is None
            assert runtime.quality.context(task['id'], config.model_copy(update={'model': 'another-model'}), initial) == ([], [])
            changed = store.commit_action(initial['id'], {'kind': 'create_element', 'args': {'type': 'ellipse'}}, {})['document']
            changed_key, _ = runtime.quality.current_review(task['id'], config, changed)
            assert runtime.quality.schema_feedback(task['id'], changed_key) is None
            assert runtime.quality.context(task['id'], config, changed) == ([], [])
            later = store.create_task(session['id'])
            runtime.quality.begin(later['id'], initial, '画一个圆')
            assert runtime.quality.schema_feedback(later['id'], key) is None
            assert store.get_document(initial['id']) == changed
        finally:
            await runtime.close()
            store.close()

    asyncio.run(check())


def test_current_review_context_survives_compaction_but_not_scene_config_or_task_changes(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'current-review.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        config = ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434')
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            doc = store.save_snapshot(session['document']['id'], 0, document()['elements'], [])
            runtime.quality.begin(task['id'], session['document'], '画图')
            key, cached = runtime.quality.current_review(task['id'], config, doc)
            assert cached is None
            # Replay an actual recorded response to test cache/context transport,
            # not to assert visual acceptance of this unrelated test canvas.
            fixture = json.loads((Path(__file__).parent / 'fixtures/quality-local-edit-response.json').read_text(encoding='utf-8'))
            assessment, failure = parse_assessment_response(fixture['response'])
            assert failure is None
            result = {'ok': True, 'review_key': key, 'scene_key': review_scene_key(doc), 'assessment': assessment.model_dump()}
            store.log_tool(task['id'], 'quality', 0, 'quality_review', {'review_key': key, 'result': result})
            compacted = runtime.memory.compact(task['id'], 'main', {'goal': '画图', 'expected_revision': 0}, doc)
            assert compacted['ok']
            messages, keys = runtime.quality.context(task['id'], config, doc)
            prepared = runtime.memory.prepare_messages(task['id'], 'main', f"main:{task['id']}", 'state', doc, []) + messages
            assert keys == [key] and json.loads(prepared[-1]['content'])['current_drawing_review']['assessment'] == assessment.model_dump()
            assert runtime.quality.context(task['id'], config.model_copy(update={'model': 'another-model'}), doc) == ([], [])
            assert runtime.quality.context(task['id'], config, {**doc, 'selection': [doc['elements'][0]['id']]}) == ([], [])
            changed = store.commit_action(doc['id'], {'kind': 'move_elements', 'args': {
                'ids': [doc['elements'][0]['id']], 'dx': 20, 'dy': 0}}, {})['document']
            assert runtime.quality.context(task['id'], config, changed) == ([], [])
            later = store.create_task(session['id'])
            runtime.quality.begin(later['id'], doc, '继续画')
            assert runtime.quality.context(later['id'], config, doc) == ([], [])
            other = store.create_session()
            with pytest.raises(ValueError, match='another session'):
                runtime.quality.context(task['id'], config, other['document'])
        finally:
            await runtime.close()
            store.close()

    asyncio.run(check())


def test_stage_review_waits_for_this_tasks_running_and_pending_children(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'settled.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            assert runtime._child_work_settled(session['id'], task['id'])
            child = native_drawing_child(store, runtime, session, task)
            assert not runtime._child_work_settled(session['id'], task['id'])
            assert child.call('create_element', {'type': 'ellipse'}, 'circle')['ok']
            ready = store.agents.complete_run(session['id'], child.run['id'], '已准备')
            assert not runtime._child_work_settled(session['id'], task['id'])
            store.agents.reject_result(session['id'], ready['result_id'], '本次不用')
            assert runtime._child_work_settled(session['id'], task['id'])
            later = store.create_task(session['id'])
            native_drawing_child(store, runtime, session, later)
            assert runtime._child_work_settled(session['id'], task['id'])
            assert not runtime._child_work_settled(session['id'], later['id'])
        finally:
            await runtime.close()
            store.close()

    asyncio.run(check())

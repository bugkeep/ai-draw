import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest

from studio.actions import apply_action, create_native_element
from studio.quality import DrawingQuality, drawing_change_summary, parse_assessment_response, review_scene_key
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


@pytest.mark.parametrize('kind', ['malformed', 'local-edit'])
def test_actual_recorded_reviewer_responses_cannot_turn_invalid_json_into_approval(kind):
    fixture = json.loads((Path(__file__).parent / 'fixtures' / f'quality-{kind}-response.json').read_text(encoding='utf-8'))
    raw = fixture['response']['tool_calls'][0]['function']['arguments']
    assert hashlib.sha256(raw.encode()).hexdigest() == fixture['argument_sha256']
    assessment, failure = parse_assessment_response(fixture['response'])
    if kind == 'malformed':
        assert assessment is None and failure['ok'] is False
        assert failure['review_response'] == fixture['response'] and 'invalid assessment' in failure['error']
    else:
        assert failure is None and assessment.goal_satisfied
        assert all(not criterion.applicable or criterion.passed for criterion in assessment.criteria)

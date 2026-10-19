import math

import pytest

from studio.actions import apply_action, element_fingerprint
from studio.agents import prepare_drawing_tool
from studio.store import StudioStore


def drawing():
    document = {'id': 'drawing', 'version': 0, 'elements': [], 'selection': []}
    draft, selection, _, _ = prepare_drawing_tool('draw_paths', {'paths': [
        {'name': '待修正线条', 'points': [[0, 0], [200, 0]], 'strokeWidth': 0.5},
        {'name': '保留线条', 'points': [[0, 100], [200, 100]], 'strokeWidth': 0.3},
    ]}, document, [], [])
    return {**document, 'elements': draft, 'selection': selection}


def test_segment_eraser_preserves_both_ends_style_and_one_step_history(tmp_path):
    store = StudioStore(tmp_path / 'eraser.sqlite3')
    session = store.create_session('局部擦除')
    scene = drawing()
    store.save_snapshot(session['document']['id'], 0, scene['elements'], scene['selection'])
    before = store.get_document(session['document']['id'])
    target, untouched = before['elements']
    draft, _, actions, output = prepare_drawing_tool('erase_strokes', {
        'points': [[100, -20], [100, 20]], 'radius': 10, 'ids': [target['id']],
    }, before, before['elements'], before['selection'])
    assert len(actions) == 1
    active = [item for item in draft if not item.get('isDeleted') and item['id'] != untouched['id']]
    assert len(active) == 2 and active[0]['id'] == target['id']
    assert max(active[0]['x'] + point[0] for point in active[0]['points']) < 90
    assert min(active[1]['x'] + point[0] for point in active[1]['points']) > 110
    assert next(item for item in draft if item['id'] == untouched['id']) == untouched
    for item in active:
        assert item['strokeColor'] == target['strokeColor'] and item['strokeWidth'] == target['strokeWidth']
        assert len(item['points']) == len(item['pressures'])
    assert set(output['changed_ids']) == {item['id'] for item in active}
    committed = store.commit_action(before['id'], actions[0]['action'], actions[0]['expected_elements'])['document']
    assert [element_fingerprint(item) for item in committed['elements']] == [element_fingerprint(item) for item in draft]
    assert store.history_step(before['id'], 'undo')['elements'] == before['elements']
    assert store.history_step(before['id'], 'redo')['elements'] == committed['elements']
    store.close()


def test_rotated_stroke_is_erased_in_canvas_coordinates_and_locked_stroke_is_preserved():
    scene = drawing()
    target, locked = scene['elements']
    target['angle'] = math.pi / 2
    locked['locked'] = True
    draft, _, actions, _ = prepare_drawing_tool('erase_strokes', {
        'points': [[100, 0]], 'radius': 12,
    }, scene, scene['elements'], scene['selection'])
    assert len(actions) == 1
    fragments = [item for item in draft if item['id'] != locked['id']]
    assert len(fragments) == 2 and all(item['angle'] == 0 for item in fragments)
    assert all(abs(item['x'] + point[0] - 100) < 1e-6 for item in fragments for point in item['points'])
    assert next(item for item in draft if item['id'] == locked['id']) == locked


def test_whole_stroke_eraser_and_empty_gesture_keep_unrelated_content():
    scene = drawing()
    target, untouched = scene['elements']
    draft, selection, _, _ = prepare_drawing_tool('erase_strokes', {
        'points': [[50, 0]], 'radius': 3, 'mode': 'stroke',
    }, scene, scene['elements'], [target['id'], untouched['id']])
    assert draft[0]['isDeleted'] and draft[1] == untouched
    assert selection == [untouched['id']]
    unchanged, selected, _, output = prepare_drawing_tool('erase_strokes', {
        'points': [[300, 300]], 'radius': 3,
    }, scene, scene['elements'], scene['selection'])
    assert unchanged == scene['elements'] and selected == scene['selection'] and output['changed_ids'] == []


def test_eraser_validation_is_atomic_and_fragment_ids_are_stable():
    scene = drawing()
    for arguments in ({'points': [[math.inf, 0]]}, {'points': [[0, 0]], 'radius': -1},
                      {'points': [[0, 0]], 'ids': ['missing']}):
        with pytest.raises(ValueError):
            prepare_drawing_tool('erase_strokes', arguments, scene, scene['elements'], [])
    _, _, prepared, _ = prepare_drawing_tool('erase_strokes', {
        'points': [[100, -20], [100, 20]], 'radius': 5,
    }, scene, scene['elements'], [])
    first, _, _ = apply_action(scene['elements'], [], prepared[0]['action'])
    second, _, _ = apply_action(scene['elements'], [], prepared[0]['action'])
    assert [element_fingerprint(item) for item in first] == [element_fingerprint(item) for item in second]
    assert not any(item['isDeleted'] for item in scene['elements'])


def test_prepared_eraser_cannot_touch_strokes_added_after_its_snapshot(tmp_path):
    store = StudioStore(tmp_path / 'parallel-eraser.sqlite3')
    session = store.create_session('并行绘图')
    initial = drawing()
    store.save_snapshot(session['document']['id'], 0, initial['elements'], initial['selection'])
    before = store.get_document(session['document']['id'])
    _, _, prepared, _ = prepare_drawing_tool('erase_strokes', {
        'points': [[100, 0]], 'radius': 10,
    }, before, before['elements'], [])
    added = store.commit_action(before['id'], {'kind': 'create_element', 'args': {
        'type': 'freedraw', 'points': [[0, 0], [200, 0]], 'name': '其他 Agent 后续笔画',
    }}, {})['document']['elements'][-1]
    after = store.commit_action(before['id'], prepared[0]['action'], prepared[0]['expected_elements'])['document']
    assert next(item for item in after['elements'] if item['id'] == added['id']) == added
    store.close()

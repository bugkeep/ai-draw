import asyncio
import io

import pytest
from PIL import Image
from shapely import Point

from studio.actions import apply_action, create_native_element, element_fingerprint
from studio.agents import prepare_drawing_tool, scene_context
from studio.eraser import element_geometry, filled_path
from studio.renderer import CanvasRenderer
from studio.store import StudioStore


FRAME = {'x': 0, 'y': 0, 'width': 200, 'height': 100}


def drawing():
    background = create_native_element({'type': 'line', 'name': '背景竖框', 'strokeColor': '#2563eb',
        'strokeWidth': 6, 'points': [[20, 50], [180, 50]]})
    base = {'id': 'canvas', 'version': 0, 'elements': [background], 'selection': []}
    scene, selected, _, output = prepare_drawing_tool('draw_paths', {'paths': [{
        'name': '前景轮廓', 'points': [[70, 30], [130, 30], [130, 70], [70, 70]],
        'smooth': False, 'closed': True, 'backgroundColor': '#ffffff'}]}, base, base['elements'], [])
    return {**base, 'elements': scene, 'selection': selected}, output['changed_ids'][0]


def pixel(rendered, x=100, y=50):
    with Image.open(io.BytesIO(rendered['png'])) as image:
        bounds = rendered['scene_bounds']
        px = round((x - bounds['x']) / bounds['width'] * image.width)
        py = round((y - bounds['y']) / bounds['height'] * image.height)
        return image.convert('RGB').getpixel((px, py))


def test_real_editor_fill_and_layer_order_change_visible_pixels_and_restore_with_one_undo(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'fill-layers.sqlite3')
        renderer = CanvasRenderer()
        try:
            session = store.create_session('遮挡与图层')
            scene, foreground_id = drawing()
            before = store.save_snapshot(session['document']['id'], 0, scene['elements'], [foreground_id])
            assert before['elements'][1]['type'] == 'freedraw' and filled_path(before['elements'][1])
            assert pixel(await renderer.render(before, FRAME)) == (255, 255, 255)
            prepared, _, actions, output = prepare_drawing_tool('reorder_elements', {
                'ids': [foreground_id], 'position': 'back'}, before, before['elements'], before['selection'])
            assert output['changed_ids'] == [foreground_id]
            after = store.commit_action(before['id'], actions[0]['action'], actions[0]['expected_elements'])['document']
            assert after['elements'] == prepared
            assert pixel(await renderer.render(after, FRAME)) == (37, 99, 235)
            assert {item['id']: item for item in after['elements']} == {item['id']: item for item in before['elements']}
            event = store.events_after(before['id'], 1)[0]['payload']['patch']
            assert event['upserts'] == [] and event['order'] == [item['id'] for item in after['elements']]
            undone = store.history_step(before['id'], 'undo')
            assert undone['elements'] == before['elements'] and pixel(await renderer.render(undone, FRAME)) == (255, 255, 255)
            redone = store.history_step(before['id'], 'redo')
            assert redone['elements'] == after['elements']
            restored = store.commit_action(before['id'], {'kind': 'reorder_elements', 'args': {
                'ids': [foreground_id], 'position': 'front'}}, {})['document']
            assert restored['elements'] == before['elements']
        finally:
            await renderer.close()
            store.close()

    asyncio.run(check())


def test_filled_contour_reshape_keeps_id_style_and_closed_boundary_until_explicitly_opened():
    scene, foreground_id = drawing()
    foreground = scene['elements'][1]
    reshaped, _, _, _ = prepare_drawing_tool('reshape_path', {
        'element_id': foreground_id, 'points': [[70, 30], [130, 30], [100, 80]], 'smooth': False,
    }, scene, scene['elements'], scene['selection'])
    assert reshaped[1]['id'] == foreground_id and reshaped[1]['backgroundColor'] == '#ffffff'
    assert reshaped[1]['points'][0] == reshaped[1]['points'][-1] and filled_path(reshaped[1])
    assert len(reshaped[1]['pressures']) == len(reshaped[1]['points'])
    assert scene_context({**scene, 'elements': reshaped}, ids=[foreground_id],
        region={'x': 98, 'y': 48, 'width': 4, 'height': 4})['elements'][0]['id'] == foreground_id
    opened, _, _, _ = prepare_drawing_tool('reshape_path', {'element_id': foreground_id,
        'points': [[70, 30], [130, 30], [100, 80]], 'smooth': False, 'closed': False},
        scene, reshaped, scene['selection'])
    assert opened[1]['backgroundColor'] == 'transparent' and not filled_path(opened[1])
    assert scene['elements'][1] == foreground


def test_global_segment_eraser_preserves_filled_foreground_and_covered_background_but_can_target_hidden_ids():
    scene, foreground_id = drawing()
    background, foreground = scene['elements']
    assert filled_path(foreground) and Point(100, 50).within(element_geometry(foreground))
    unchanged, _, _, output = prepare_drawing_tool('erase_strokes', {
        'points': [[100, 50]], 'radius': 3}, scene, scene['elements'], scene['selection'])
    assert unchanged == scene['elements'] and not output['changed_ids']
    targeted, _, _, output = prepare_drawing_tool('erase_strokes', {
        'points': [[100, 50]], 'radius': 3, 'ids': [background['id']]}, scene, scene['elements'], scene['selection'])
    assert output['changed_ids'] and all(item == foreground for item in targeted if item['id'] == foreground_id)
    erased, _, _, _ = prepare_drawing_tool('erase_strokes', {
        'points': [[100, 50]], 'radius': 3, 'ids': [foreground_id], 'mode': 'stroke'},
        scene, scene['elements'], scene['selection'])
    assert erased[1]['isDeleted'] and element_fingerprint(erased[0]) == element_fingerprint(background)
    with pytest.raises(ValueError, match='filled shapes'):
        prepare_drawing_tool('erase_strokes', {'points': [[100, 50]], 'ids': [foreground_id]},
            scene, scene['elements'], scene['selection'])


def test_segment_sweep_only_cuts_exposed_background_and_respects_translucent_fills():
    scene, _ = drawing()
    background, foreground = scene['elements']
    clipped, _, _, _ = prepare_drawing_tool('erase_strokes', {
        'points': [[20, 50], [180, 50]], 'radius': 3}, scene, scene['elements'], [])
    remaining = [element for element in clipped if element['id'] != foreground['id'] and not element.get('isDeleted')]
    assert len(remaining) == 1
    assert min(remaining[0]['x'] + point[0] for point in remaining[0]['points']) == 70
    assert max(remaining[0]['x'] + point[0] for point in remaining[0]['points']) == 130
    translucent = [{**element, **({'opacity': 50} if element['id'] == foreground['id'] else {})} for element in scene['elements']]
    exposed, _, _, output = prepare_drawing_tool('erase_strokes', {
        'points': [[100, 50]], 'radius': 3}, scene, translucent, [])
    assert background['id'] in output['changed_ids']
    assert next(element for element in exposed if element['id'] == foreground['id'])['opacity'] == 50


def test_prepared_visible_erase_preserves_occlusion_when_replayed_and_committed(tmp_path):
    scene, foreground_id = drawing()
    store = StudioStore(tmp_path / 'visible-erase.sqlite3')
    try:
        session = store.create_session()
        before = store.save_snapshot(session['document']['id'], 0, scene['elements'], [])
        prepared, _, actions, _ = prepare_drawing_tool('erase_strokes', {
            'points': [[20, 50], [180, 50]], 'radius': 3}, before, before['elements'], [])
        action = actions[0]
        assert action['action']['args']['ids'] == [scene['elements'][0]['id']]
        assert action['action']['args']['visible_only'] is True
        committed = store.commit_action(before['id'], action['action'], action['expected_elements'])['document']
        def content(items):
            return [{key: value for key, value in item.items() if key not in {'updated', 'versionNonce'}} for item in items]
        assert content(committed['elements']) == content(prepared)
        assert next(item for item in committed['elements'] if item['id'] == foreground_id) == before['elements'][1]
        assert committed['elements'][0]['width'] == 60
        assert store.history_step(before['id'], 'undo')['elements'] == before['elements']
        assert store.history_step(before['id'], 'redo')['elements'] == committed['elements']
    finally:
        store.close()


def test_filled_path_validation_and_reordering_are_atomic_and_do_not_move_locked_targets():
    scene, foreground_id = drawing()
    for args in ({'paths': [{'name': '未闭合', 'points': [[0, 0], [30, 20]], 'backgroundColor': '#ffffff'}]},
                 {'backgroundColor': '#12', 'paths': [{'name': '无效颜色', 'points': [[0, 0], [20, 10]]}]}):
        with pytest.raises(ValueError):
            prepare_drawing_tool('draw_paths', args, scene, scene['elements'], [])
    with pytest.raises(ValueError, match='not found'):
        apply_action(scene['elements'], [], {'kind': 'reorder_elements', 'args': {
            'ids': [foreground_id, 'missing'], 'position': 'back'}})
    locked = [{**element, 'locked': True} for element in scene['elements']]
    result, selected, changed = apply_action(locked, [foreground_id], {'kind': 'reorder_elements', 'args': {
        'ids': [foreground_id], 'position': 'back'}})
    assert result == locked and selected == [foreground_id] and changed == []
    result, _, _ = apply_action(scene['elements'], [], {'kind': 'reorder_elements', 'args': {
        'ids': [foreground_id, foreground_id], 'position': 'back'}})
    assert [element['id'] for element in result] == [foreground_id, scene['elements'][0]['id']]

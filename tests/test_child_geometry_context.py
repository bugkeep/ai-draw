import asyncio
import copy
import json

import pytest
from shapely import Point

from studio.child_drawing_memory import drawing_memory_context
from studio.child_geometry_context import no_effect_erase_context
from studio.coordinates import map_observed_arguments
from studio.eraser import element_geometry
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore


def two_strokes(child):
    output = child.call('draw_paths', {'paths': [
        {'name': 'upper contour', 'smooth': False, 'points': [[20, 20], [180, 20]]},
        {'name': 'lower contour', 'smooth': False, 'points': [[20, 80], [180, 80]]}]}, 'actual-contours')
    assert output['ok']
    return output['changed_ids']


def test_actual_no_effect_erase_identifies_a_current_target_and_the_next_native_erase_changes_it(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'targeting.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            wrong, target = two_strokes(child)
            arguments = {'ids': [wrong], 'points': [[80, 80], [120, 80]], 'radius': 1, 'mode': 'segment'}
            receipt = child.call('erase_strokes', arguments, 'missed-current-line')
            assert receipt['ok'] and receipt['action_count'] == 0
            before = copy.deepcopy(child.run)
            writes = store.connection.total_changes
            journal = json.loads(drawing_memory_context(runtime.memory, child.run, [])['content'])['drawing_execution_memory']
            observation = journal['erase_targeting']
            assert observation['call_id'] == 'missed-current-line' and observation['memory_ref'].startswith('memory_')
            assert store.connection.total_changes == writes and child.run == before
            assert observation['selected_geometry'][0]['distance_to_attempted_gesture'] == 60
            assert [item['id'] for item in observation['nearest_geometry']] == [target]
            nearest = observation['nearest_geometry'][0]
            assert nearest['distance_to_attempted_gesture'] == 0
            assert nearest['nearby_path_parts'] and not nearest['dense_path_parts_omitted']
            assert all(element_geometry(next(item for item in child.run['draft'] if item['id'] == target)).distance(
                Point(point)) < 1e-9 for part in nearest['nearby_path_parts'] for point in part['points'])
            assert element_geometry(next(item for item in child.run['draft'] if item['id'] == target)).distance(
                Point(nearest['nearest_native_point'])) == 0
            original_wrong = copy.deepcopy(next(item for item in child.run['draft'] if item['id'] == wrong))
            changed = child.call('erase_strokes', {'ids': [target], 'points': nearest['nearby_path_parts'][0]['points'],
                'radius': 1, 'mode': 'segment'}, 'actual-corrected-hit')
            assert changed['ok'] and changed['action_count'] > 0
            assert next(item for item in child.run['draft'] if item['id'] == wrong) == original_wrong
            assert 'erase_targeting' not in json.loads(drawing_memory_context(runtime.memory, child.run, [])['content'])[
                'drawing_execution_memory']
            assert store.get_document(session['document']['id'])['elements'] == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_real_image_metadata_and_moved_rotated_geometry_provide_canvas_hit_points(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'image-targeting.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            wrong, target = two_strokes(child)
            assert child.call('move_elements', {'ids': [target], 'dx': 5, 'dy': 12}, 'actual-move')['ok']
            assert child.call('update_elements', {'ids': [target], 'patch': {'angle': 0.35}}, 'actual-rotate')['ok']
            _, ids = await runtime.vision.context(task['id'], child.run['agent_id'], child.run['id'], child.document)
            metadata = json.loads(store.connection.execute('SELECT metadata FROM canvas_observations WHERE id=?', (ids[0],)).fetchone()[0])
            area = metadata['scene_bounds']
            points = [[(x - area['x']) / area['width'] * 1000, (y - area['y']) / area['height'] * 1000]
                      for x, y in ((105, 92), (125, 92))]
            original = {'ids': [wrong], 'points': points, 'radius': 1, 'mode': 'segment',
                'coordinate_space': 'image_normalized', 'observation_id': ids[0]}
            mapped = map_observed_arguments('erase_strokes', original, metadata)
            receipt = child.call('erase_strokes', mapped, 'actual-mapped-miss')
            assert receipt['ok'] and receipt['action_count'] == 0
            before = copy.deepcopy((child.run, original, metadata, receipt))
            observed = no_effect_erase_context(child.run['draft'], original, receipt, metadata)
            assert (child.run, original, metadata, receipt) == before
            nearest = observed['nearest_geometry'][0]
            assert nearest['id'] == target and nearest['distance_to_attempted_gesture'] < 1e-9
            geometry = element_geometry(next(item for item in child.run['draft'] if item['id'] == target))
            assert geometry.distance(Point(nearest['nearest_native_point'])) < 1e-9
            with pytest.raises(ValueError, match='original observation metadata'):
                no_effect_erase_context(child.run['draft'], original, receipt)
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_native_geometry_ties_and_agent_isolation_remain_explicit(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'ties.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        other_session = store.create_session()
        other_task = store.create_task(other_session['id'])
        other = native_drawing_child(store, runtime, other_session, other_task)
        try:
            wrong, target = two_strokes(child)
            duplicate = child.call('draw_paths', {'paths': [{'name': 'shared contour', 'smooth': False,
                'points': [[20, 80], [180, 80]]}]}, 'actual-coincident-contour')['changed_ids'][0]
            foreign_output = other.call('draw_paths', {'paths': [{'name': 'foreign contour',
                'points': [[20, 80], [180, 80]]}]}, 'foreign-line')
            assert foreign_output['ok']
            foreign = foreign_output['changed_ids'][0]
            arguments = {'ids': [wrong], 'points': [[100, 80]], 'radius': 1, 'mode': 'segment'}
            receipt = child.call('erase_strokes', arguments, 'actual-tie-miss')
            observed = no_effect_erase_context(child.run['draft'], arguments, receipt)
            assert {item['id'] for item in observed['nearest_geometry']} == {target, duplicate}
            assert all(item['id'] != foreign for item in observed['nearest_geometry'])
            assert [item['id'] for item in observed['nearest_geometry']] == [duplicate, target]
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_actual_tool_errors_keep_their_error_and_do_not_become_no_effect_observations(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'errors.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            rectangle = child.call('create_element', {'type': 'rectangle', 'backgroundColor': '#ffffff'}, 'actual-filled-shape')['changed_ids'][0]
            arguments = {'ids': [rectangle], 'points': [[10, 10]], 'radius': 1, 'mode': 'segment'}
            failure = child.call('erase_strokes', arguments, 'actual-unsupported-segment')
            assert not failure['ok']
            with pytest.raises(ValueError, match='successful no-change receipt'):
                no_effect_erase_context(child.run['draft'], arguments, failure)
            journal = json.loads(drawing_memory_context(runtime.memory, child.run, [])['content'])['drawing_execution_memory']
            assert 'erase_targeting' not in journal
            stored = json.loads(store.connection.execute('SELECT output FROM agent_tool_calls WHERE call_id=?',
                ('actual-unsupported-segment',)).fetchone()[0])
            assert stored == failure
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())

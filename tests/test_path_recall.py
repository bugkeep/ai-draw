import asyncio
import copy
import math

import pytest
from shapely import LineString

from studio.actions import create_native_element
from studio.agent_tools import validate_tool
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore


def test_dense_curve_recall_preserves_error_budget_pressure_mapping_exact_retrieval_and_history(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'curve-recall.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            curve = create_native_element({'type': 'freedraw', 'points': [[index / 3, 25 * math.sin(index / 50)]
                for index in range(1201)], 'name': '密集曲线'})
            curve.update(angle=0.7, pressures=[0.25 + index / 2400 for index in range(1201)])
            document = store.save_snapshot(session['document']['id'], 0, [curve], [])
            original = copy.deepcopy(document)
            history = store.get_history(document['id'], 'undo')
            points, indices, pressures = [], [], []
            args = {'element_id': curve['id'], 'point_limit': 7, 'point_tolerance': 1}
            while args:
                result = runtime.memory.recall(task['id'], 'main', args, document)
                assert not result['geometry_exact'] and result['source_point_count'] == 1201
                assert result['element']['angle'] == curve['angle']
                points.extend(result['element']['points'])
                indices.extend(result['source_point_indices'])
                pressures.extend(result['element']['pressures'])
                args = validate_tool('recall_memory', result['next_page']) if result['next_page'] else None
            assert len(points) < 120 and points == [curve['points'][index] for index in indices]
            assert pressures == [curve['pressures'][index] for index in indices]
            assert LineString(points).hausdorff_distance(LineString(curve['points'])) <= 1
            exact_points, exact_pressures = [], []
            args = {'element_id': curve['id'], 'point_limit': 256}
            while args:
                exact = runtime.memory.recall(task['id'], 'main', args, document)
                assert exact['geometry_exact']
                exact_points.extend(exact['element']['points'])
                exact_pressures.extend(exact['element']['pressures'])
                args = validate_tool('recall_memory', exact['next_page']) if exact['next_page'] else None
            assert exact_points == curve['points'] and exact_pressures == curve['pressures']
            assert document == original == store.get_document(document['id'])
            assert store.get_history(document['id'], 'undo') == history
        finally:
            await runtime.close()
            store.close()

    asyncio.run(check())


def test_sampled_closed_contour_keeps_closure_and_pages_cannot_mix_changed_paths_or_tolerances(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'closed-recall.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            points = [[100 * math.cos(index * 2 * math.pi / 600), 80 * math.sin(index * 2 * math.pi / 600)] for index in range(600)]
            points.append(points[0])
            curve = create_native_element({'type': 'freedraw', 'points': points, 'backgroundColor': '#ffffff'})
            doc = store.save_snapshot(session['document']['id'], 0, [curve], [])
            first = runtime.memory.recall(task['id'], 'main', {'element_id': curve['id'],
                'point_limit': 3, 'point_tolerance': 1}, doc)
            assert first['next_page'] and first['source_point_indices'][0] == 0
            args = first['next_page']
            with pytest.raises(ValueError, match='sampling options changed'):
                runtime.memory.recall(task['id'], 'main', {**args, 'point_tolerance': 2}, doc)
            moved = store.commit_action(doc['id'], {'kind': 'move_elements', 'args': {
                'ids': [curve['id']], 'dx': 20, 'dy': 0}}, {})['document']
            with pytest.raises(ValueError, match='path or sampling options changed'):
                runtime.memory.recall(task['id'], 'main', args, moved)
            fresh = runtime.memory.recall(task['id'], 'main', {'element_id': curve['id'],
                'point_limit': 256, 'point_tolerance': 1}, moved)
            assert fresh['element']['points'][0] == fresh['element']['points'][-1]
            assert fresh['source_point_indices'][-1] == len(points) - 1
            assert LineString(fresh['element']['points']).hausdorff_distance(LineString(curve['points'])) <= 1
        finally:
            await runtime.close()
            store.close()

    asyncio.run(check())

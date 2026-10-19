import asyncio
import base64
import io
import json

import pytest
from PIL import Image
from shapely import LineString

from studio.actions import validate_action
from studio.agent_tools import CHILD_TOOLS, MAIN_TOOLS, validate_tool
from studio.agents import DrawingAgents, prepare_drawing_tool
from studio.eraser import element_geometry
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore
from studio.vision import CanvasVision


def pixels(metadata, points):
    return [[(x - metadata['scene_bounds']['x']) * metadata['pixels_per_unit']['x'],
             (y - metadata['scene_bounds']['y']) * metadata['pixels_per_unit']['y']] for x, y in points]


@pytest.mark.parametrize('coordinate_space', ['image_pixels', 'image_normalized'])
def test_actual_cropped_png_maps_draw_reshape_and_erase_to_exact_native_canvas(tmp_path, coordinate_space):
    async def check():
        store = StudioStore(tmp_path / 'coordinates.sqlite3')
        vision = CanvasVision(store)
        try:
            session = store.create_session('负坐标下的局部修正')
            task = store.create_task(session['id'])
            child = store.agents.create_agent(session['id'], task['id'], 'geometry', '修复曲线', 'Artist', [])
            caller = child['run_id']
            document = session['document']
            draft, _, actions, _ = prepare_drawing_tool('draw_paths', {'paths': [
                {'name': '原有曲线', 'points': [[-280, 180], [-240, 210], [-200, 180]], 'smooth': False},
            ]}, document, [], [])
            document = store.commit_action(document['id'], actions[0]['action'], {})['document']
            original = draft[0]
            region = {'x': -320, 'y': 140, 'width': 241, 'height': 200}
            observed = await vision.observe(task['id'], child['agent_id'], caller, document, region)
            assert observed['scene_bounds'] == region
            assert observed['pixels_per_unit']['x'] != observed['pixels_per_unit']['y']  # real PNG rounding
            _, delivered = await vision.context(task['id'], child['agent_id'], caller, document)
            assert observed['observation_id'] in delivered
            vision.delivered(task['id'], caller, delivered)
            px, py = pixels(observed, [[-240, 210]])[0]
            with Image.open(io.BytesIO(vision.image(session['id'], observed['observation_id']))) as image:
                assert image.convert('L').crop((int(px)-3, int(py)-3, int(px)+4, int(py)+4)).getextrema()[0] < 160

            def image_points(metadata, anchors):
                if coordinate_space == 'image_pixels':
                    return pixels(metadata, anchors)
                bounds = metadata['scene_bounds']
                return [[(x-bounds['x']) / bounds['width'] * 1000,
                         (y-bounds['y']) / bounds['height'] * 1000] for x, y in anchors]

            # Exact pixel positions come from the actual rendered PNG metadata,
            # not an assumed square viewport or an invented model response.
            anchors = [[-275, 185], [-235, 195], [-195, 185]]
            args = validate_tool('reshape_path', {'observation_id': observed['observation_id'],
                'coordinate_space': coordinate_space,
                'element_id': original['id'], 'points': image_points(observed, anchors), 'smooth': False}, child=True)
            mapped, evidence = vision.ground_tool(task['id'], caller, document, 'reshape_path', args)
            draft, selection, prepared, _ = prepare_drawing_tool('reshape_path', mapped, document, document['elements'], [])
            assert element_geometry(draft[0]).hausdorff_distance(LineString(anchors)) < 1e-8
            assert draft[0]['id'] == original['id'] and draft[0]['strokeWidth'] == original['strokeWidth']
            assert evidence['coordinate_frame']['observation_id'] == observed['observation_id']
            assert evidence['coordinate_frame']['input_space'] == coordinate_space
            assert 'observation_id' not in prepared[0]['action']['args']
            assert 'coordinate_space' not in prepared[0]['action']['args']
            changed = store.commit_action(document['id'], prepared[0]['action'], prepared[0]['expected_elements'])['document']
            with pytest.raises(ValueError, match='drawing changed'):
                vision.ground_tool(task['id'], caller, changed, 'reshape_path', args)
            assert store.history_step(document['id'], 'undo')['elements'] == document['elements']
            assert store.history_step(document['id'], 'redo')['elements'] == changed['elements']

            seen = await vision.observe(task['id'], child['agent_id'], caller, changed, region)
            vision.delivered(task['id'], caller, [seen['observation_id']])
            draw = validate_tool('draw_paths', {'observation_id': seen['observation_id'], 'strokeWidth': .25,
                'coordinate_space': coordinate_space,
                'paths': [{'name': '局部补线', 'points': image_points(seen, [[-270, 235], [-200, 235]]), 'smooth': False}]}, child=True)
            normalized, _ = vision.ground_tool(task['id'], caller, changed, 'draw_paths', draw)
            new_draft, _, _, _ = prepare_drawing_tool('draw_paths', normalized, changed, changed['elements'], [])
            assert element_geometry(new_draft[-1]).hausdorff_distance(LineString([[-270, 235], [-200, 235]])) < 1e-8
            assert new_draft[-1]['strokeWidth'] == .25
            erase = validate_tool('erase_strokes', {'observation_id': seen['observation_id'],
                'coordinate_space': coordinate_space,
                'points': image_points(seen, [[-235, 195]]), 'radius': 10, 'ids': [original['id']]}, child=True)
            normalized, _ = vision.ground_tool(task['id'], caller, changed, 'erase_strokes', erase)
            expected_radius = (10 / min(seen['pixels_per_unit'].values()) if coordinate_space == 'image_pixels'
                               else 10 * max(seen['scene_bounds']['width'], seen['scene_bounds']['height']) / 1000)
            assert normalized['radius'] == pytest.approx(expected_radius)
            erased, _, erase_actions, _ = prepare_drawing_tool('erase_strokes', normalized, changed, changed['elements'], [])
            surviving = [element_geometry(item) for item in erased if not item.get('isDeleted')]
            assert len(surviving) == 2
            assert all(geometry.distance(LineString([[-235, 194], [-235, 196]])) > 1 for geometry in surviving)
            final = store.commit_action(changed['id'], erase_actions[0]['action'], erase_actions[0]['expected_elements'])['document']
            assert store.history_step(changed['id'], 'undo')['elements'] == changed['elements']
            assert store.history_step(changed['id'], 'redo')['elements'] == final['elements']
        finally:
            await vision.close()
            store.close()
    asyncio.run(check())


def test_image_frames_require_own_delivered_current_canvas_and_reject_ambiguous_input(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'frame-isolation.sqlite3')
        vision = CanvasVision(store)
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            caller = f"main:{task['id']}"
            document = session['document']
            seen = await vision.observe(task['id'], 'main', caller, document)
            args = validate_tool('erase_strokes', {'observation_id': seen['observation_id'], 'points': [[20, 20]]})
            with pytest.raises(ValueError, match='not reached'):
                vision.ground_tool(task['id'], caller, document, 'erase_strokes', args)
            vision.delivered(task['id'], caller, [seen['observation_id']])
            for other_caller in ('unrelated-child-run', 'main:other-task'):
                with pytest.raises(ValueError, match='another Agent/task'):
                    vision.ground_tool(task['id'], other_caller, document, 'erase_strokes', args)
            other_task = store.create_task(session['id'])
            with pytest.raises(ValueError, match='another Agent/task'):
                vision.ground_tool(other_task['id'], caller, document, 'erase_strokes', args)
            other_document = store.create_session()['document']
            with pytest.raises(ValueError, match='another session'):
                vision.ground_tool(task['id'], caller, other_document, 'erase_strokes', args)
            before = store.get_document(document['id'])
            for points in ([[-1, 10]], [[seen['width'] + .1, 10]], [[10, seen['height'] + .1]]):
                with pytest.raises(ValueError, match='inside the observed PNG'):
                    vision.ground_tool(task['id'], caller, document, 'erase_strokes', {**args, 'points': points})
            assert store.get_document(document['id']) == before
            for transform in ({'origin': [1, 0]}, {'scale': 2}):
                with pytest.raises(ValueError, match='origin/scale'):
                    validate_tool('draw_paths', {'observation_id': seen['observation_id'], **transform,
                        'paths': [{'name': 'ambiguous', 'points': [[0, 0], [20, 20]]}]}, child=True)
            for tool, arguments in [('draw_paths', {'paths': [{'name': 'p', 'points': [[0, 0], [10, 10]]}]}),
                                    ('reshape_path', {'element_id': 'p', 'points': [[0, 0], [10, 10]]}),
                                    ('erase_strokes', {'points': [[100, 100]]})]:
                with pytest.raises(ValueError, match='coordinate_space requires'):
                    validate_tool(tool, {**arguments, 'coordinate_space': 'image_normalized'}, child=True)
            with pytest.raises(ValueError, match='0..1000'):
                vision.ground_tool(task['id'], caller, document, 'erase_strokes',
                    {**args, 'coordinate_space': 'image_normalized', 'points': [[1001, 10]]})
            # A proposal may have exactly the same pixels but is not a tool frame.
            proposal_caller = 'proposal-only'
            proposal = await vision.observe(task['id'], 'main', proposal_caller, document, result_id='pending')
            vision.delivered(task['id'], proposal_caller, [proposal['observation_id']])
            with pytest.raises(ValueError, match='proposed result'):
                vision.ground_tool(task['id'], proposal_caller, document, 'erase_strokes',
                    {**args, 'observation_id': proposal['observation_id']})
            with pytest.raises(ValueError):
                validate_action({'kind': 'erase_strokes', 'args': args})
            with pytest.raises(ValueError, match='Resolve observed'):
                prepare_drawing_tool('erase_strokes', args, document, [], [])
        finally:
            await vision.close()
            store.close()
    asyncio.run(check())


def test_main_eraser_grounds_pixels_before_transaction_and_replays_cached_call(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'observed-eraser.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            document = store.commit_action(session['document']['id'], {'kind': 'create_element', 'args': {
                'type': 'line', 'name': '需擦除的短线', 'points': [[-200, 300], [-100, 300]],
            }}, {})['document']
            observed = await runtime.vision.observe(task['id'], 'main', f"main:{task['id']}", document,
                {'x': -220, 'y': 280, 'width': 140, 'height': 40})
            runtime.vision.delivered(task['id'], f"main:{task['id']}", [observed['observation_id']])
            call = {'id': 'observed-erase', 'function': {'name': 'erase_strokes', 'arguments': json.dumps({
                'observation_id': observed['observation_id'], 'points': pixels(observed, [[-150, 300]]),
                'radius': 30, 'mode': 'segment', 'ids': [document['elements'][0]['id']],
            })}}
            models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            output = await runtime.call_main_tool(models, task['id'], session['id'], document['id'], call)
            assert output['ok'] and output['coordinate_frame']['observation_id'] == observed['observation_id']
            final = store.get_document(document['id'])
            assert final['version'] == document['version'] + 1
            # The original image is stale now, but a saved call is replayed without
            # resolving/reapplying it against the changed scene.
            repeated = await runtime.call_main_tool(models, task['id'], session['id'], document['id'], call)
            assert repeated == output and store.get_document(document['id']) == final
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_all_advertised_image_coordinate_schemas_match_tool_validation():
    for tools in (MAIN_TOOLS, CHILD_TOOLS):
        for definition in tools:
            name = definition['function']['name']
            if name in {'draw_paths', 'reshape_path', 'erase_strokes'}:
                assert 'observation_id' in definition['function']['parameters']['properties']


def test_focused_current_images_refresh_without_another_tool_call_and_remain_private(tmp_path):
    async def check():
        database = tmp_path / 'focused-views.sqlite3'
        store = StudioStore(database)
        vision = CanvasVision(store)
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            caller = f"main:{task['id']}"
            document = store.commit_action(session['document']['id'], {'kind': 'create_element', 'args': {
                'type': 'ellipse', 'x': -300, 'y': 100, 'width': 60, 'height': 40,
                'backgroundColor': '#2563eb',
            }}, {})['document']
            focus = {'x': -330, 'y': 80, 'width': 130, 'height': 90}
            old = await vision.observe(task['id'], 'main', caller, document, focus, remember_region=True)
            _, ids = await vision.context(task['id'], 'main', caller, document)
            assert old['observation_id'] in ids and len(ids) == 2
            changed = store.commit_action(document['id'], {'kind': 'move_elements', 'args': {
                'ids': [document['elements'][0]['id']], 'dx': 10, 'dy': 5,
            }}, {})['document']
            messages, ids = await vision.context(task['id'], 'main', caller, changed)
            metadata = [json.loads(item['content'][0]['text'])['canvas_observation'] for item in messages]
            current_focus = next(item for item in metadata if item['region'] is not None)
            assert len(metadata) == 2 and old['observation_id'] not in ids
            assert current_focus['region'] == focus and current_focus['scene_hash'] != old['scene_hash']
            assert all(item['scene_hash'] == current_focus['scene_hash'] for item in metadata)
            guided_message = next(item for item in messages
                if json.loads(item['content'][0]['text'])['canvas_observation']['region'] is not None)
            guided_png = base64.b64decode(guided_message['content'][1]['image_url']['url'].split(',', 1)[1])
            assert current_focus['coordinate_guide']['units'] == 'image_normalized'
            assert guided_png != vision.image(session['id'], current_focus['observation_id'])
            with Image.open(io.BytesIO(guided_png)) as guided:
                assert guided.size == (current_focus['width'], current_focus['height'])
            assert store.get_document(document['id']) == changed
            assert vision.image(session['id'], current_focus['observation_id']) != vision.image(session['id'], old['observation_id'])
            vision.delivered(task['id'], caller, ids)
            normalized, _ = vision.ground_tool(task['id'], caller, changed, 'erase_strokes', {
                'observation_id': current_focus['observation_id'], 'points': [[200, 200]]})
            assert 'observation_id' not in normalized
            child_pictures, _ = await vision.context(task['id'], 'child', 'other-private-run', changed)
            assert len(child_pictures) == 1
            await vision.close()
            store.close()
            store = StudioStore(database)
            vision = CanvasVision(store)
            _, reopened = await vision.context(task['id'], 'main', caller, changed)
            assert reopened == ids
            await vision.observe(task['id'], 'main', caller, changed, remember_region=True)
            full_only, _ = await vision.context(task['id'], 'main', caller, changed)
            assert len(full_only) == 1
            store.update_task(task['id'], 'completed')
            store.delete_session(session['id'])
            assert not store.connection.execute('SELECT * FROM canvas_viewports').fetchall()
        finally:
            await vision.close()
            store.close()
    asyncio.run(check())

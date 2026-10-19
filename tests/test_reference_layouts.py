import asyncio
import io
import json

import pytest
from PIL import Image, ImageDraw
from shapely import LineString

from studio.agent_tools import CHILD_TOOLS, MAIN_TOOLS, validate_tool
from studio.agents import prepare_drawing_tool
from studio.eraser import element_geometry
from studio.reference_images import ReferenceImages, decode_reference
from studio.agents import DrawingAgents
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def source(images, task, *, title='native geometry fixture', color='black'):
    image = Image.new('RGB', (400, 300), 'white')
    ImageDraw.Draw(image).line([(120, 100), (200, 200), (280, 100)], fill=color, width=3)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return images.save_image(task['id'], 'native-fixture', {'title': title,
        'url': 'https://example.org/layout-source', 'image': 'https://example.org/layout-source.png'},
        buffer.getvalue(), decode_reference(buffer.getvalue()))['reference_id']


def see(images, task, caller, reference_id, region=None, *, delivered=True):
    view = images.view(task['id'], caller, {'reference_id': reference_id, 'region': region})
    if delivered:
        images.delivered(task['id'], caller, [view['reference_view_id']])
    return view


def layout(images, task, reference_id):
    caller = f"main:{task['id']}"
    see(images, task, caller, reference_id)
    return images.layouts.create(task['id'], caller, {'name': 'common composition', 'reference_id': reference_id,
        'source_frame': {'x': 40, 'y': 60, 'width': 320, 'height': 180},
        'target': {'x': -200, 'y': 100, 'width': 640, 'height': 600}})


def anchors(view, plan, points, **extra):
    return {'reference_view_id': view['reference_view_id'], 'reference_layout_id': plan['reference_layout_id'],
            'coordinate_space': 'image_normalized', 'paths': [{'name': 'source curve', 'points': points, 'smooth': False}], **extra}


def test_shared_layout_maps_full_and_cropped_sources_to_identical_native_curves_and_history(tmp_path):
    async def check():
        database = tmp_path / 'source-layout.sqlite3'
        store = StudioStore(database)
        try:
            images = ReferenceImages(store)
            session = store.create_session()
            task = store.create_task(session['id'])
            reference_id = source(images, task)
            document = session['document']
            before_history = store.get_history(document['id'], 'undo')
            plan = layout(images, task, reference_id)
            assert plan['mapping'] == {'origin': [-280, 100], 'scale': 2}
            assert layout(images, task, reference_id) == plan
            assert store.get_document(document['id']) == document and store.get_history(document['id'], 'undo') == before_history
            full = see(images, task, 'child-full', reference_id)
            see(images, task, 'child-crop', reference_id)
            crop = see(images, task, 'child-crop', reference_id, {'x': 120, 'y': 100, 'width': 160, 'height': 100})
            expected = LineString([[-40, 300], [120, 500], [280, 300]])
            arguments = anchors(crop, plan, [[0, 0], [500, 1000], [1000, 0]], strokeWidth=.25)
            args = validate_tool('draw_paths', arguments, child=True)
            mapped, evidence = images.ground_tool(task['id'], 'child-crop', document, 'draw_paths', args)
            draft, selection, actions, _ = prepare_drawing_tool('draw_paths', mapped, document, [], [])
            assert element_geometry(draft[0]).hausdorff_distance(expected) < 1e-8
            assert draft[0]['strokeWidth'] == .25 and evidence['coordinate_frame']['mapping'] == plan['mapping']
            assert not {'reference_view_id', 'reference_layout_id', 'coordinate_space'} & actions[0]['action']['args'].keys()
            full_args = anchors(full, plan, [[300, 1000/3], [500, 2000/3], [700, 1000/3]])
            mapped, _ = images.ground_tool(task['id'], 'child-full', document, 'draw_paths',
                validate_tool('draw_paths', full_args, child=True))
            full_draft, _, _, _ = prepare_drawing_tool('draw_paths', mapped, document, [], [])
            assert element_geometry(full_draft[0]).hausdorff_distance(expected) < 1e-8
            saved = store.commit_action(document['id'], actions[0]['action'], actions[0]['expected_elements'])['document']
            reshape = {key: value for key, value in arguments.items() if key not in {'paths', 'strokeWidth'}}
            reshape.update(element_id=saved['elements'][0]['id'], points=[[0, 0], [500, 900], [1000, 0]], smooth=False)
            mapped, _ = images.ground_tool(task['id'], 'child-crop', saved, 'reshape_path',
                validate_tool('reshape_path', reshape, child=True))
            replacement, _, edits, _ = prepare_drawing_tool('reshape_path', mapped, saved, saved['elements'], selection)
            assert replacement[0]['id'] == saved['elements'][0]['id'] and replacement[0]['strokeWidth'] == .25
            assert element_geometry(replacement[0]).hausdorff_distance(LineString([[-40, 300], [120, 480], [280, 300]])) < 1e-8
            final = store.commit_action(document['id'], edits[0]['action'], edits[0]['expected_elements'])['document']
            assert store.history_step(document['id'], 'undo')['elements'] == saved['elements']
            assert store.history_step(document['id'], 'redo')['elements'] == final['elements']
            # Tracing and hand-authored source anchors share the SAME mapping.
            _, trace = await images.trace(task['id'], 'child-crop', {'reference_id': reference_id,
                'name': 'same mapping', 'source_frame': plan['source_frame'], 'target': plan['target'],
                'mode': 'ink', 'min_length': 3})
            assert trace['mapping'] == plan['mapping']
            store.close()
            store = StudioStore(database)
            images = ReferenceImages(store)
            again, _ = images.ground_tool(task['id'], 'child-crop', final, 'draw_paths', args)
            assert again['paths'][0]['points'] == [(-40, 300), (120, 500), (280, 300)]
            store.update_task(task['id'], 'completed')
            store.delete_session(session['id'])
            assert store.connection.execute('SELECT count(*) FROM reference_layouts').fetchone()[0] == 0
        finally:
            store.close()
    asyncio.run(check())


def test_layout_view_delivery_scopes_future_tasks_and_source_frame_are_checked_before_drawing(tmp_path):
    store = StudioStore(tmp_path / 'layout-scopes.sqlite3')
    try:
        images = ReferenceImages(store)
        session = store.create_session()
        task = store.create_task(session['id'])
        reference_id = source(images, task)
        main = f"main:{task['id']}"
        arguments = {'name': 'unseen', 'reference_id': reference_id,
                     'target': {'x': 0, 'y': 0, 'width': 400, 'height': 300}}
        with pytest.raises(ValueError, match='full reference'):
            images.layouts.create(task['id'], main, arguments)
        see(images, task, main, reference_id, delivered=False)
        with pytest.raises(ValueError, match='full reference'):
            images.layouts.create(task['id'], main, arguments)
        plan = layout(images, task, reference_id)
        with pytest.raises(ValueError, match='positive scale'):
            images.layouts.create(task['id'], main, {**arguments,
                'target': {'x': 0, 'y': 0, 'width': 5e-324, 'height': 300}})
        with pytest.raises(ValueError, match='Only the main'):
            images.layouts.create(task['id'], 'child', arguments)
        view = see(images, task, 'child', reference_id)
        crop = see(images, task, 'child', reference_id, {'x': 120, 'y': 100, 'width': 160, 'height': 100}, delivered=False)
        args = validate_tool('draw_paths', anchors(crop, plan, [[0, 0], [1000, 1000]]), child=True)
        document = session['document']
        with pytest.raises(ValueError, match='next model context'):
            images.ground_tool(task['id'], 'child', document, 'draw_paths', args)
        images.delivered(task['id'], 'child', [crop['reference_view_id']])
        see(images, task, 'other-child', reference_id)
        with pytest.raises(ValueError, match='your own view'):
            images.ground_tool(task['id'], 'other-child', document, 'draw_paths', args)
        wrong_source = source(images, task, title='another source', color='red')
        wrong_view = see(images, task, 'child', wrong_source)
        with pytest.raises(ValueError, match='your own view'):
            images.ground_tool(task['id'], 'child', document, 'draw_paths',
                {**args, 'reference_view_id': wrong_view['reference_view_id']})
        other_canvas = store.create_session()['document']
        with pytest.raises(ValueError, match='another canvas'):
            images.ground_tool(task['id'], 'child', other_canvas, 'draw_paths', args)
        for points, error in [([[-1, 0], [100, 100]], '0..1000'), ([[0, 0], [1001, 100]], '0..1000')]:
            with pytest.raises(ValueError, match=error):
                images.ground_tool(task['id'], 'child', document, 'draw_paths',
                    validate_tool('draw_paths', anchors(crop, plan, points), child=True))
        with pytest.raises(ValueError, match='source_frame'):
            images.ground_tool(task['id'], 'child', document, 'draw_paths',
                validate_tool('draw_paths', anchors(view, plan, [[0, 0], [100, 100]]), child=True))
        later = store.create_task(session['id'])
        later_plan = layout(images, later, reference_id)
        with pytest.raises(KeyError, match='later task'):
            images.ground_tool(task['id'], 'child', document, 'draw_paths',
                {**args, 'reference_layout_id': later_plan['reference_layout_id']})
        later_view = see(images, later, 'later-child', reference_id)
        later_args = anchors(later_view, plan, [[300, 1000/3], [500, 2000/3]])
        mapped, _ = images.ground_tool(later['id'], 'later-child', document, 'draw_paths',
            validate_tool('draw_paths', later_args, child=True))
        assert LineString(mapped['paths'][0]['points']).hausdorff_distance(LineString([(-40, 300), (120, 500)])) < 1e-8
        assert store.get_document(document['id']) == document and store.get_history(document['id'], 'undo') == []
    finally:
        store.close()


def test_reference_coordinate_schema_rejects_ambiguous_or_unresolved_frames():
    pair = {'reference_view_id': 'refview_' + 'a'*32, 'reference_layout_id': 'layout_' + 'b'*32,
            'coordinate_space': 'image_normalized'}
    paths = {'paths': [{'name': 'curve', 'points': [[0, 0], [500, 500]]}]}
    for invalid in ({key: value for key, value in pair.items() if key != 'reference_layout_id'},
                    {**pair, 'observation_id': 'view_' + 'c'*32}, {**pair, 'coordinate_space': 'image_pixels'},
                    {**pair, 'origin': [1, 0]}):
        with pytest.raises(ValueError):
            validate_tool('draw_paths', {**paths, **invalid}, child=True)
    with pytest.raises(ValueError):
        validate_tool('erase_strokes', {**pair, 'points': [[0, 0]]}, child=True)
    with pytest.raises(ValueError, match='Resolve observed'):
        prepare_drawing_tool('draw_paths', {**pair, **paths}, {'id': 'canvas', 'elements': [], 'selection': []}, [], [])
    assert any(item['function']['name'] == 'create_reference_layout' for item in MAIN_TOOLS)
    assert not any(item['function']['name'] == 'create_reference_layout' for item in CHILD_TOOLS)
    with pytest.raises(ValueError):
        validate_tool('create_reference_layout', {}, child=True)


def test_layout_metadata_and_cached_main_call_do_not_break_citations_or_next_model_context(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'layout-main-context.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            reference_id = source(runtime.references, task)
            models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            # Invoke actual native tools and record their real outputs. This
            # test prepares no model reply and makes no model request.
            call = {'id': 'view-source', 'function': {'name': 'view_reference_image',
                'arguments': json.dumps({'reference_id': reference_id})}}
            viewed = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], call)
            runtime.references.delivered(task['id'], f"main:{task['id']}", [viewed['reference_view_id']])
            call = {'id': 'define-layout', 'function': {'name': 'create_reference_layout', 'arguments': json.dumps({
                'name': 'source composition', 'reference_id': reference_id,
                'target': {'x': -200, 'y': 100, 'width': 640, 'height': 600}})}}
            layout_output = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], call)
            assert layout_output['ok'] and layout_output['reference_layout_id'].startswith('layout_')
            # A reference_id can occur in mapping metadata without a citation
            # title/url. The original view still supplies the real citation.
            citations = store.research_references(task['id'])
            assert citations == [{'reference_id': reference_id, 'title': 'native geometry fixture',
                                  'url': 'https://example.org/layout-source'}]
            messages = models.main_messages(task['id'], 'draw the source', session['document'], [], [])
            assert json.loads(messages[1]['content'])['research_references'] == citations
            repeated = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], call)
            assert repeated == layout_output
            assert store.connection.execute('SELECT count(*) FROM reference_layouts').fetchone()[0] == 1
            assert store.get_document(session['document']['id']) == session['document']
            assert store.get_history(session['document']['id'], 'undo') == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())

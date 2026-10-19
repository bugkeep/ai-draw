import asyncio
import io
import json
import math

import pytest
from PIL import Image, ImageDraw
from shapely import LineString, Polygon

from studio.memory import AgentMemory
from studio.agent_store import stable_id
from studio.reference_images import ReferenceImages, decode_reference
from studio.store import StudioStore


def study(images, task):
    image = Image.new('RGB', (256, 256), 'white')
    drawing = ImageDraw.Draw(image)
    drawing.line([(150, 32), (190, 110), (110, 110), (150, 32)], fill='black', width=3)
    drawing.line([(95, 25), (125, 25)], fill='black', width=3)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return images.save_image(task['id'], 'main', {'title': '主体轮廓与边角背景',
        'url': 'https://example.org/subject-study', 'image': 'https://example.org/subject-study.png'},
        buffer.getvalue(), decode_reference(buffer.getvalue()))


def present(images, task, source):
    caller = f"main:{task['id']}"
    images.view(task['id'], caller, {'reference_id': source['reference_id']})
    _, ids = images.context(task['id'], caller)
    images.delivered(task['id'], caller, ids)
    return caller


def arguments(source, **extra):
    return {'reference_id': source['reference_id'], 'name': '主体', 'mode': 'ink', 'sigma': 0,
        'min_length': 5, 'simplify': 0.5, 'target': {'x': 100, 'y': 200, 'width': 512, 'height': 256}, **extra}


def test_polygon_excludes_rectangular_corner_background_and_retains_mapping_and_immutable_pages(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'polygon.sqlite3')
        images = ReferenceImages(store)
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            source = study(images, task)
            caller = present(images, task, source)
            region = {'x': 90, 'y': 20, 'width': 120, 'height': 110}
            polygon = [(150, 20), (210, 130), (90, 130)]
            rectangular, rectangle = await images.trace(task['id'], caller, arguments(source, region=region))
            masked, mask = await images.trace(task['id'], caller, arguments(source, region=region, source_polygon=polygon))
            assert len(masked) < len(rectangular)
            assert mask['mapping'] == rectangle['mapping'] and mask['trace_id'] == rectangle['trace_id']
            assert mask['trace_plan_id'] != rectangle['trace_plan_id'] and mask['selection_hash']
            origin, scale = mask['mapping']['origin'], mask['mapping']['scale']
            area = Polygon([(origin[0] + x * scale, origin[1] + y * scale) for x, y in polygon])
            assert all(area.buffer(0.002).covers(LineString(item['points'])) for item in masked)
            assert any(not area.buffer(0.002).covers(LineString(item['points'])) for item in rectangular)
            first, page = await images.trace(task['id'], caller, arguments(source,
                region=region, source_polygon=polygon, limit=1))
            all_items = first
            while page['next_page']:
                items, page = await images.continue_trace(task['id'], caller, page['next_page'])
                all_items += items
                assert page['selection_hash'] == mask['selection_hash'] and page['mapping'] == mask['mapping']
            assert all_items == masked
            assert max(math.dist(first, last) for item in masked
                       for first, last in zip(item['points'], item['points'][1:])) <= 2.00001
            unmasked_plan = json.loads(store.connection.execute('SELECT arguments FROM reference_trace_plans WHERE id=?',
                (rectangle['trace_plan_id'],)).fetchone()[0])
            assert 'source_polygon' not in unmasked_plan  # retain the pre-existing no-mask plan representation
            assert store.get_document(session['document']['id'])['elements'] == []
            assert store.connection.execute('SELECT count(*) FROM reference_traces').fetchone()[0] == 1
        finally:
            store.close()

    asyncio.run(check())


def test_existing_trace_plan_continues_with_its_original_sparse_point_representation(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'legacy-plan.sqlite3')
        images = ReferenceImages(store)
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            source = study(images, task)
            caller = present(images, task, source)
            _, page = await images.trace(task['id'], caller, arguments(source, limit=1))
            current = json.loads(store.connection.execute('SELECT arguments FROM reference_trace_plans WHERE id=?',
                (page['trace_plan_id'],)).fetchone()[0])
            assert current.pop('native_format_version') == 1
            encoded = json.dumps(current, sort_keys=True)
            legacy_id = stable_id('traceplan', f"{task['id']}/{encoded}")
            with store.connection:
                store.connection.execute('INSERT INTO reference_trace_plans VALUES(?,?,?,?,?)',
                    (legacy_id, source['reference_id'], task['id'], encoded, 0))
            legacy, old_page = await images.continue_trace(task['id'], caller, {
                'trace_plan_id': legacy_id, 'offset': 0, 'limit': 1})
            fresh, new_page = await images.continue_trace(task['id'], caller, {
                'trace_plan_id': page['trace_plan_id'], 'offset': 0, 'limit': 1})
            assert old_page['total_paths'] == new_page['total_paths']
            assert old_page['mapping'] == new_page['mapping']
            assert legacy[0]['points'][0] == fresh[0]['points'][0]
            assert len(legacy[0]['points']) < len(fresh[0]['points'])
            assert LineString(legacy[0]['points']).hausdorff_distance(LineString(fresh[0]['points'])) < 1e-10
            assert store.get_document(session['document']['id']) == session['document']
        finally:
            store.close()

    asyncio.run(check())


@pytest.mark.parametrize('polygon,message', [
    ([(20, 20), (100, 100), (20, 100), (100, 20)], 'non-self-intersecting'),
    ([(20, 20), (50, 50), (100, 100)], 'non-self-intersecting'),
    ([(0, 0), (300, 0), (100, 100)], 'source_frame'),
    ([(10, 10), (10, 10), (20, 20)], 'three distinct'),
])
def test_invalid_subject_polygon_creates_no_cached_geometry_plan_or_canvas_change(tmp_path, polygon, message):
    async def check():
        store = StudioStore(tmp_path / 'invalid-polygon.sqlite3')
        images = ReferenceImages(store)
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            source = study(images, task)
            caller = present(images, task, source)
            with pytest.raises(ValueError, match=message):
                await images.trace(task['id'], caller, arguments(source, source_polygon=polygon))
            assert store.get_document(session['document']['id']) == session['document']
            for table in ('reference_trace_plans', 'reference_traces'):
                assert store.connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
        finally:
            store.close()

    asyncio.run(check())


def test_masked_and_unmasked_trace_ranges_remain_separate_after_memory_compaction(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'mask-memory.sqlite3')
        images, memory = ReferenceImages(store), AgentMemory(store)
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            source = study(images, task)
            caller = present(images, task, source)
            for index, polygon in enumerate([None, [(150, 20), (210, 130), (90, 130)]]):
                args = arguments(source, source_polygon=polygon)
                _, output = await images.trace(task['id'], caller, args)
                output = {'ok': True, **output}
                call_id = f'trace-{index}'
                store.agents.begin_call(task['id'], caller, call_id, 'trace_reference', json.dumps(args))
                store.agents.finish_call(caller, call_id, output)
                memory.for_context(task['id'], 'main', caller, call_id, 'trace_reference', json.dumps(args), output, 'same-canvas')
            document = store.get_document(session['document']['id'])
            memory.compact(task['id'], 'main', {'goal': '分别描主体与全图', 'summary': '两种选区保留独立记录'}, document)
            traces = memory.snapshot(task['id'], 'main', caller, 'same-canvas', document)['execution_history']['reference_traces']
            assert traces['family_count'] == 2
            assert all(family['repeated_path_count'] == 0 for family in traces['families'])
            assert sum(bool(family.get('selection_hash')) for family in traces['families']) == 1
        finally:
            store.close()

    asyncio.run(check())

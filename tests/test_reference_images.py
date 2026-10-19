import asyncio
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from server.app import create_app
from studio.actions import element_fingerprint
from studio.agent_tools import validate_tool
from studio.agents import DrawingAgents, prepare_drawing_tool
from studio.reference_images import ReferenceImages, decode_reference, page_image_candidates
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def line_image(format='PNG', *, mode='RGB'):
    image = Image.new(mode, (256, 256), 'white')
    drawing = ImageDraw.Draw(image)
    drawing.line([(32, 64), (224, 64)], fill='black', width=5)
    drawing.line([(128, 160), (128, 224)], fill='black', width=5)
    buffer = io.BytesIO()
    image.save(buffer, format=format)
    return buffer.getvalue()


def save_source(images, task, agent_id='main'):
    data = line_image()
    return images.save_image(task['id'], agent_id, {
        'title': '两条独立线条', 'url': 'https://example.org/line-study', 'image': 'https://example.org/line-study.png',
    }, data, decode_reference(data))


def trace_args(source, **extra):
    return {'reference_id': source['reference_id'], 'name': '手部', 'mode': 'ink', 'sigma': 0,
            'min_length': 5, 'simplify': 0.5,
            'target': {'x': 100, 'y': 200, 'width': 512, 'height': 256}, **extra}


def present(images, task, caller_id, source):
    images.view(task['id'], caller_id, {'reference_id': source['reference_id']})
    messages, view_ids = images.context(task['id'], caller_id)
    assert messages[-1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    images.delivered(task['id'], caller_id, view_ids)
    return view_ids


@pytest.mark.parametrize('format', ['PNG', 'JPEG', 'WEBP', 'GIF', 'BMP', 'TIFF'])
def test_reference_decodes_actual_raster_formats_without_changing_dimensions(format):
    encoded = line_image(format)
    png, metadata = decode_reference(encoded)
    with Image.open(io.BytesIO(png)) as decoded:
        assert decoded.size == (256, 256) and decoded.mode == 'RGB'
    assert metadata['format'] == format and metadata['original_size'] == [256, 256]


def test_reference_orientation_transparency_and_invalid_payloads():
    image = Image.new('RGB', (320, 160), 'white')
    ImageDraw.Draw(image).line([(30, 60), (290, 60)], fill='black', width=3)
    exif = Image.Exif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', exif=exif)
    png, metadata = decode_reference(buffer.getvalue())
    assert (metadata['width'], metadata['height']) == (160, 320)
    with Image.open(io.BytesIO(png)) as decoded:
        assert decoded.getexif().get(274) is None
    rgba = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(rgba).line([(30, 64), (220, 64)], fill='black', width=3)
    buffer = io.BytesIO()
    rgba.save(buffer, format='PNG')
    png, _ = decode_reference(buffer.getvalue())
    with Image.open(io.BytesIO(png)) as decoded:
        assert decoded.getpixel((0, 0)) == (255, 255, 255)
    with pytest.raises(OSError):
        decode_reference(b'<html>Not image pixels</html>')
    with pytest.raises(ValueError, match='20 MB'):
        decode_reference(b'x' * 20_000_001)
    tiny = io.BytesIO()
    Image.new('RGB', (50, 50), 'white').save(tiny, format='PNG')
    with pytest.raises(ValueError, match='too small'):
        decode_reference(tiny.getvalue())


def test_illustrated_page_uses_metadata_and_subject_images_with_relative_urls():
    content = b'''<html><head><title>Mountain drawing tutorial</title>
        <meta property="og:title" content="Mountain drawing tutorial">
        <meta property="og:description" content="Draw a mountain contour">
        <meta property="og:image" content="/images/mountain-final.webp"></head>
        <body><img src="/logo.png" alt="Site logo">
        <img src="/images/mountain-final.webp" alt="Mountain contour">
        <img data-src="https://cdn.example.org/mountain-step.png" alt="Mountain drawing steps"></body></html>'''
    candidates = page_image_candidates(content, 'https://example.org/tutorial/mountains',
        {'title': 'Search title'}, ['mountain'])
    assert [item['image'] for item in candidates] == [
        'https://example.org/images/mountain-final.webp', 'https://cdn.example.org/mountain-step.png']
    assert all(item['title'] == 'Mountain drawing tutorial' for item in candidates)
    with pytest.raises(ValueError, match='does not match'):
        page_image_candidates(content, 'https://example.org/tutorial/mountains', {'title': 'Search title'}, ['hands'])
    with pytest.raises(ValueError, match='no usable subject image'):
        page_image_candidates(b'<html><head><title>Mountain study</title></head><body>Mountain study</body></html>',
                              'https://example.org/mountain', {'title': 'Search title'}, ['mountain'])


def test_reference_pixels_views_and_traces_are_scoped_persistent_and_removed_with_canvas(tmp_path):
    async def check():
        path = tmp_path / 'reference.sqlite3'
        store = StudioStore(path)
        images = ReferenceImages(store)
        session = store.create_session('参考图片独立保存')
        other = store.create_session('其他画布')
        task, other_task = store.create_task(session['id']), store.create_task(other['id'])
        other_before = store.get_session(other['id'])
        caller = f"main:{task['id']}"
        source = save_source(images, task)
        assert save_source(images, task)['reference_id'] == source['reference_id']
        assert store.get_document(session['document']['id'])['elements'] == []
        with pytest.raises(KeyError, match='another canvas'):
            images.view(other_task['id'], 'other', {'reference_id': source['reference_id']})
        with pytest.raises(KeyError, match='this canvas'):
            images.image(other['id'], source['reference_id'])
        images.view(task['id'], caller, {'reference_id': source['reference_id']})
        with pytest.raises(ValueError, match='own model context'):
            await images.trace(task['id'], caller, trace_args(source))
        views = present(images, task, caller, source)
        with pytest.raises(ValueError, match='another Agent'):
            images.delivered(task['id'], 'another-child', views)
        with pytest.raises(ValueError, match='own model context'):
            await images.trace(task['id'], 'another-child', trace_args(source))
        items, trace = await images.trace(task['id'], caller, trace_args(source))
        assert len(items) == trace['total_paths'] == 2
        future = store.create_task(session['id'])
        altered = line_image('JPEG')
        future_source = images.save_image(future['id'], 'main', {
            'title': '稍后输入的参考图片', 'url': 'https://example.org/later', 'image': 'https://example.org/later.jpg',
        }, altered, decode_reference(altered))
        with pytest.raises(KeyError, match='later task'):
            images.view(task['id'], caller, {'reference_id': future_source['reference_id']})
        assert future_source['reference_id'] not in json.dumps(images.context(task['id'], caller)[0])
        store.close()
        reopened = StudioStore(path)
        retained = ReferenceImages(reopened)
        try:
            repeated, again = await retained.trace(task['id'], caller, trace_args(source))
            assert repeated == items and again['cached_geometry'] and again['trace_id'] == trace['trace_id']
            assert retained.context(task['id'], caller)[1] == views
            assert retained.image(session['id'], source['reference_id']).startswith(b'\x89PNG')
            reopened.update_task(task['id'], 'completed')
            reopened.update_task(future['id'], 'completed')
            reopened.delete_session(session['id'])
            for table in ('reference_images', 'reference_image_views', 'reference_traces'):
                assert reopened.connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
            assert reopened.get_session(other['id']) == other_before
            assert reopened.connection.execute('PRAGMA foreign_key_check').fetchall() == []
        finally:
            reopened.close()
    asyncio.run(check())


def test_trace_pages_regions_and_native_edits_keep_common_full_source_mapping(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'mapping.sqlite3')
        images = ReferenceImages(store)
        try:
            session = store.create_session('区域轮廓的共同坐标')
            task = store.create_task(session['id'])
            caller = f"main:{task['id']}"
            source = save_source(images, task)
            present(images, task, caller, source)
            full, metadata = await images.trace(task['id'], caller, trace_args(source))
            assert metadata['mapping'] == {'origin': [228.0, 200.0], 'scale': 1.0}
            first, page = await images.trace(task['id'], caller, trace_args(source, limit=1))
            second, final = await images.trace(task['id'], caller, trace_args(source, offset=page['next_offset'], limit=1))
            assert first + second == full and final['next_offset'] is None
            cropped, crop = await images.trace(task['id'], caller, trace_args(source,
                region={'x': 64, 'y': 32, 'width': 64, 'height': 64}))
            assert crop['mapping'] == metadata['mapping'] and len(cropped) == 1
            assert min(point[0] for point in cropped[0]['points']) == 292
            assert max(point[0] for point in cropped[0]['points']) == 356
            with pytest.raises(ValueError, match='within'):
                await images.trace(task['id'], caller, trace_args(source, region={'x': 250, 'y': 0, 'width': 20, 'height': 40}))
            with pytest.raises(ValueError, match='offset'):
                await images.trace(task['id'], caller, trace_args(source, offset=2))
            document = store.get_document(session['document']['id'])
            _, _, prepared, _ = prepare_drawing_tool('create_elements', {'items': full}, document, [], [])
            for item in prepared:
                store.commit_action(document['id'], item['action'], {})
            before = store.get_document(document['id'])
            assert all(item['type'] == 'freedraw' and len(item['points']) == len(item['pressures'])
                       for item in before['elements'])
            target, unrelated = before['elements']
            moved = store.commit_action(document['id'], {'kind': 'move_elements', 'args': {
                'ids': [target['id']], 'dx': 5, 'dy': 0}}, {})['document']
            assert moved['elements'][0]['id'] == target['id'] and moved['elements'][0]['x'] == target['x'] + 5
            assert element_fingerprint(unrelated) == element_fingerprint(moved['elements'][1])
            store.history_step(document['id'], 'undo')
            erase = store.commit_action(document['id'], {'kind': 'erase_strokes', 'args': {
                'points': [[350, 264]], 'radius': 10, 'ids': [target['id']], 'mode': 'segment'}}, {})['document']
            assert len([item for item in erase['elements'] if not item.get('isDeleted')]) == 3
            assert element_fingerprint(unrelated) == element_fingerprint(next(
                item for item in erase['elements'] if item['id'] == unrelated['id']))
            assert store.history_step(document['id'], 'undo')['elements'] == before['elements']
            assert store.history_step(document['id'], 'redo')['elements'] == erase['elements']
        finally:
            store.close()
    asyncio.run(check())


def test_trace_main_runtime_is_idempotent_and_reference_endpoint_checks_ownership(tmp_path, monkeypatch):
    async def check():
        store = StudioStore(tmp_path / 'runtime.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        try:
            session = store.create_session('主 Agent 原生轮廓')
            task = store.create_task(session['id'])
            source = save_source(runtime.references, task)
            present(runtime.references, task, f"main:{task['id']}", source)
            models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            call = {'id': 'trace-call', 'type': 'function', 'function': {
                'name': 'trace_reference', 'arguments': json.dumps(trace_args(source))}}
            output = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], call)
            assert output['ok'] and output['returned_paths'] == len(output['changed_ids']) == 2
            before = store.get_document(session['document']['id'])
            assert await runtime.call_main_tool(models, task['id'], session['id'], before['id'], call) == output
            assert store.get_document(before['id']) == before
            assert 'base64' not in json.dumps(store.tool_logs(task['id']))
            assert '"points"' not in json.dumps(output)
            assert runtime.memory.search(task['id'], 'main', {'query': '手部'}, before)['objects']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
    monkeypatch.setenv('DRAW_AGENT_DB', str(tmp_path / 'http.sqlite3'))
    app = create_app()
    with TestClient(app) as client:
        store = app.state.studio_store
        session, other = store.create_session('参考来源'), store.create_session('独立画布')
        task = store.create_task(session['id'])
        source = save_source(app.state.drawing_runtime.references, task)
        endpoint = f"/api/studio/sessions/{session['id']}/reference-images/{source['reference_id']}"
        assert client.get(endpoint).headers['content-type'] == 'image/png'
        assert client.get(endpoint.replace(session['id'], other['id'])).status_code == 404


def test_reference_tools_validate_parameters_and_children_cannot_delegate():
    for name in ('search_reference_images', 'view_reference_image', 'trace_reference'):
        if name == 'search_reference_images':
            args = {'query': 'mountain pencil', 'subject_terms': ['mountain'], 'goal': '山水轮廓'}
        else:
            args = {'reference_id': 'image_' + 'a' * 32}
            if name == 'trace_reference':
                args = trace_args(args)
        assert validate_tool(name, args, child=True)
    with pytest.raises(ValueError, match='cannot create'):
        validate_tool('spawn_agent', {'message': '递归任务'}, child=True)
    with pytest.raises(ValueError, match='low_threshold'):
        validate_tool('trace_reference', trace_args({'reference_id': 'image_' + 'a' * 32}, low_threshold=0.9))

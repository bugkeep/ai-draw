import asyncio
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from server.app import create_app
from studio.actions import element_fingerprint
from studio.agent_tools import validate_tool
from studio.agents import DrawingAgents, prepare_drawing_tool
from studio.reference_images import ReferenceImages, commons_image_candidates, decode_reference, page_image_candidates
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


def test_actual_commons_catalogue_response_preserves_search_rank_image_sources_and_attribution():
    fixture = json.loads((Path(__file__).parent / 'fixtures/commons-imageinfo.json').read_text(encoding='utf-8'))
    assert fixture['source_url'].startswith('https://commons.wikimedia.org/w/api.php?')
    results = commons_image_candidates(fixture['response'])
    pages = sorted(fixture['response']['query']['pages'], key=lambda item: item.get('index', 0))
    assert [item['title'] for item in results] == [item['title'].removeprefix('File:') for item in pages]
    assert any(item['mime'] == 'application/pdf' for item in results)  # filtered by search, not mistaken for a portrait
    actual_images = [item for item in results if item['mime'].startswith('image/')]
    assert len(actual_images) == 5
    assert all(item['url'].startswith('https://commons.wikimedia.org/wiki/') and item['image'].startswith('https://')
               and item['license'] != 'unverified' and item['artist'] and '<' not in item['artist'] for item in actual_images)
    args = {'query': 'Mona Lisa engraving', 'subject_terms': ['Mona Lisa'], 'goal': '人物与交叠双手', 'source': 'commons'}
    assert validate_tool('search_reference_images', args, child=True)['source'] == 'commons'


@pytest.mark.parametrize('payload', [[], {'query': None}, {'query': {'pages': [None]}},
    {'error': {'code': 'ratelimited', 'info': 'Retry later'}},
    {'query': {'pages': [{'title': 'File:test', 'imageinfo': 'invalid'}]}}])
def test_invalid_catalogue_data_is_an_explicit_error(payload):
    with pytest.raises(ValueError, match='Commons catalogue'):
        commons_image_candidates(payload)


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
            for table in ('reference_images', 'reference_image_views', 'reference_traces', 'reference_search_previews', 'reference_trace_plans'):
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
            second, final = await images.continue_trace(task['id'], caller, page['next_page'])
            assert first + second == full and final['next_offset'] is None
            assert final['next_page'] is None and final['trace_plan_id'] == page['trace_plan_id']
            cropped, crop = await images.trace(task['id'], caller, trace_args(source,
                region={'x': 64, 'y': 32, 'width': 64, 'height': 64}))
            assert crop['mapping'] == metadata['mapping'] and len(cropped) == 1
            assert min(point[0] for point in cropped[0]['points']) == 292
            assert max(point[0] for point in cropped[0]['points']) == 356
            with pytest.raises(ValueError, match='within'):
                await images.trace(task['id'], caller, trace_args(source, region={'x': 250, 'y': 0, 'width': 20, 'height': 40}))
            with pytest.raises(ValueError, match='offset'):
                await images.trace(task['id'], caller, trace_args(source, offset=2))
            with pytest.raises(ValueError, match='offset'):
                await images.continue_trace(task['id'], caller, {'trace_plan_id': page['trace_plan_id'], 'offset': 2})
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


def test_trace_common_source_crop_excludes_border_and_preserves_regional_coordinates(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'source-crop.sqlite3')
        images = ReferenceImages(store)
        try:
            session = store.create_session('参考画面裁剪')
            task = store.create_task(session['id'])
            caller = f"main:{task['id']}"
            with Image.open(io.BytesIO(line_image())) as image:
                ImageDraw.Draw(image).rectangle((4, 4, 251, 251), outline='black', width=3)
                data = io.BytesIO()
                image.save(data, format='PNG')
            source = images.save_image(task['id'], 'main', {
                'title': '带边框的两条线条', 'url': 'https://example.org/bordered-study',
                'image': 'https://example.org/bordered-study.png'}, data.getvalue(), decode_reference(data.getvalue()))
            present(images, task, caller, source)
            frame = {'x': 16, 'y': 16, 'width': 224, 'height': 224}
            args = trace_args(source, source_frame=frame,
                target={'x': 100, 'y': 200, 'width': 448, 'height': 448})
            full, metadata = await images.trace(task['id'], caller, args)
            assert len(full) == 2 and metadata['source_frame'] == frame
            assert metadata['mapping'] == {'origin': [68.0, 168.0], 'scale': 2.0}
            upper, first = await images.trace(task['id'], caller, {
                **args, 'region': {'x': 16, 'y': 16, 'width': 224, 'height': 100}})
            lower, second = await images.trace(task['id'], caller, {
                **args, 'region': {'x': 16, 'y': 116, 'width': 224, 'height': 124}})
            assert [item['points'] for item in upper + lower] == [item['points'] for item in full]
            assert first['mapping'] == second['mapping'] == metadata['mapping']
            assert first['trace_id'] == second['trace_id'] == metadata['trace_id']
            assert first['cached_geometry'] and second['cached_geometry']
            assert all(100 <= x <= 548 and 200 <= y <= 648 for item in full for x, y in item['points'])
            with pytest.raises(ValueError, match='source_frame'):
                await images.trace(task['id'], caller, {**args, 'region': {'x': 0, 'y': 32, 'width': 100, 'height': 100}})
            with pytest.raises(ValueError, match='within'):
                await images.trace(task['id'], caller, {**args, 'source_frame': {'x': 100, 'y': 0, 'width': 200, 'height': 200}})
        finally:
            store.close()
    asyncio.run(check())


def test_local_contrast_recovers_actual_low_contrast_contours_and_uses_separate_cache(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'contrast.sqlite3')
        images = ReferenceImages(store)
        try:
            session = store.create_session('低对比度轮廓')
            task = store.create_task(session['id'])
            caller = f"main:{task['id']}"
            image = Image.new('RGB', (256, 256), (150, 150, 150))
            ImageDraw.Draw(image).line([(32, 64), (224, 64)], fill=(135, 135, 135), width=5)
            data = io.BytesIO()
            image.save(data, format='PNG')
            source = images.save_image(task['id'], 'main', {
                'title': '低对比度线条', 'url': 'https://example.org/contrast',
                'image': 'https://example.org/contrast.png'}, data.getvalue(), decode_reference(data.getvalue()))
            present(images, task, caller, source)
            args = trace_args(source, mode='edges', sigma=1.6)
            with pytest.raises(ValueError, match='No contours'):
                await images.trace(task['id'], caller, args)
            paths, first = await images.trace(task['id'], caller, {**args, 'contrast': 'local'})
            assert paths and first['returned_paths'] == len(paths)
            repeated, second = await images.trace(task['id'], caller, {**args, 'contrast': 'local'})
            assert repeated == paths and second['cached_geometry'] and first['trace_id'] == second['trace_id']
            assert all(250 < y < 275 for item in paths for _, y in item['points'])
        finally:
            store.close()
    asyncio.run(check())


def test_candidate_previews_are_owned_and_do_not_replace_full_source_inspection(tmp_path):
    async def check():
        path = tmp_path / 'candidate-previews.sqlite3'
        store = StudioStore(path)
        images = ReferenceImages(store)
        session = store.create_session('候选图片对比')
        task = store.create_task(session['id'])
        caller = f"main:{task['id']}"
        source = save_source(images, task)
        image = Image.new('RGB', (256, 320), 'white')
        ImageDraw.Draw(image).ellipse((40, 40, 200, 280), outline='black', width=3)
        data = io.BytesIO()
        image.save(data, format='PNG')
        second = images.save_image(task['id'], 'main', {
            'title': '椭圆结构', 'url': 'https://example.org/oval', 'image': 'https://example.org/oval.png'},
            data.getvalue(), decode_reference(data.getvalue()))
        output = {'ok': True, 'query': 'shape study', 'goal': '比较实际形状', 'sources': [source, second]}
        preview = await images._preview(task['id'], caller, output)
        messages, view_ids = images.context(task['id'], caller)
        assert view_ids == [preview['preview_id']]
        metadata = json.loads(messages[-1]['content'][0]['text'])['reference_candidates']
        assert [(item['label'], item['reference_id']) for item in metadata['sources']] == [
            ('A', source['reference_id']), ('B', second['reference_id'])]
        images.delivered(task['id'], caller, view_ids)
        with pytest.raises(ValueError, match='own model context'):
            await images.trace(task['id'], caller, trace_args(source))
        assert images.context(task['id'], 'other-agent')[1] == []
        followup = store.create_task(session['id'])
        followup_caller = f"main:{followup['id']}"
        prepared, prepared_ids = await images.prepare_context(followup['id'], followup_caller)
        assert prepared_ids and prepared_ids[0] != preview['preview_id']
        assert 'reference_candidates' in json.dumps(prepared)
        images.delivered(followup['id'], followup_caller, prepared_ids)
        with pytest.raises(ValueError, match='own model context'):
            await images.trace(followup['id'], followup_caller, trace_args(source))
        other = store.create_session('其他画布')
        other_task = store.create_task(other['id'])
        with pytest.raises(KeyError, match='another canvas'):
            await images._preview(other_task['id'], 'other-agent', output)
        repeated = await images._preview(task['id'], caller, {**output, 'cached': True})
        assert repeated['preview_id'] == preview['preview_id']
        assert store.connection.execute('SELECT count(*) FROM reference_search_previews').fetchone()[0] == 2
        full = present(images, task, caller, source)
        messages, current_ids = images.context(task['id'], caller)
        assert current_ids == full
        assert 'reference_candidates' not in json.dumps(messages)
        assert (await images.trace(task['id'], caller, trace_args(source)))[0]
        store.close()
        reopened = StudioStore(path)
        restored = ReferenceImages(reopened)
        try:
            assert restored.context(task['id'], caller)[1] == full
            again = await restored._preview(task['id'], caller, output)
            assert again['preview_id'] == preview['preview_id']
            assert preview['preview_id'] in restored.context(task['id'], caller)[1]
            store_row = reopened.connection.execute('SELECT png,delivered_at FROM reference_search_previews').fetchone()
            with Image.open(io.BytesIO(store_row['png'])) as actual:
                assert actual.size == (800, 560)
            assert store_row['delivered_at'] is not None
            reopened.update_task(task['id'], 'completed')
            reopened.update_task(followup['id'], 'completed')
            reopened.delete_session(session['id'])
            assert reopened.connection.execute('SELECT count(*) FROM reference_search_previews').fetchone()[0] == 0
        finally:
            reopened.close()
    asyncio.run(check())


def test_trace_plan_continuation_preserves_parameters_and_checks_scope_and_full_view(tmp_path):
    async def check():
        path = tmp_path / 'trace-plans.sqlite3'
        store = StudioStore(path)
        images = ReferenceImages(store)
        session = store.create_session('分页计划')
        task = store.create_task(session['id'])
        caller = f"main:{task['id']}"
        source = save_source(images, task)
        present(images, task, caller, source)
        first, page = await images.trace(task['id'], caller, trace_args(source, limit=1))
        plan_id = page['trace_plan_id']
        with pytest.raises(ValueError, match='own model context'):
            await images.continue_trace(task['id'], 'child', page['next_page'])
        present(images, task, 'child', source)
        second, final = await images.continue_trace(task['id'], 'child', page['next_page'])
        assert final['mapping'] == page['mapping'] and final['source_region'] == page['source_region']
        assert second[0]['name'] == '手部/轮廓-0002'
        assert second[0]['strokeWidth'] == first[0]['strokeWidth']
        with pytest.raises(ValueError, match='sigma'):
            validate_tool('continue_reference_trace', {**page['next_page'], 'sigma': 5})
        with pytest.raises(ValueError, match='offset'):
            validate_tool('trace_reference', trace_args(source, offset=page['next_offset']))
        other = store.create_session('独立画布')
        other_task = store.create_task(other['id'])
        with pytest.raises(KeyError, match='another canvas'):
            await images.continue_trace(other_task['id'], 'main', page['next_page'])
        future = store.create_task(session['id'])
        future_caller = f"main:{future['id']}"
        present(images, future, future_caller, source)
        _, future_page = await images.trace(future['id'], future_caller, trace_args(source, name='后续计划', limit=1))
        with pytest.raises(KeyError, match='later task'):
            await images.continue_trace(task['id'], caller, future_page['next_page'])
        store.close()
        reopened = StudioStore(path)
        restored = ReferenceImages(reopened)
        try:
            continued, metadata = await restored.continue_trace(task['id'], 'child', {'trace_plan_id': plan_id, 'offset': 1})
            assert continued == second and metadata['trace_plan_id'] == plan_id
            assert metadata['cached_geometry'] and metadata['next_page'] is None
            reopened.update_task(task['id'], 'completed')
            reopened.update_task(future['id'], 'completed')
            reopened.delete_session(session['id'])
            assert reopened.connection.execute('SELECT count(*) FROM reference_trace_plans').fetchone()[0] == 0
        finally:
            reopened.close()
    asyncio.run(check())

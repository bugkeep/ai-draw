import asyncio
import copy
import json

import pytest

from studio.actions import create_native_element
from studio.agents import DrawingAgents, prepare_drawing_tool, scene_context
from studio.runtime import DrawingRuntime
from studio.memory import tool_summary
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def drawing():
    elements = [create_native_element({'type': 'rectangle', 'name': f'Face stroke {index}',
        'x': 10 if index < 35 else 300, 'y': index * 2, 'width': 5, 'height': 1}) for index in range(45)]
    elements[5]['isDeleted'] = True
    return {'id': 'native-canvas', 'version': 7, 'elements': elements, 'selection': [elements[-1]['id']]}


def inspect(document, arguments):
    return prepare_drawing_tool('inspect_scene', arguments, document,
                                document['elements'], document['selection'])[3]['scene']


def test_filtered_pages_retrieve_every_current_object_without_repeating_or_modifying_it():
    document = drawing()
    original = copy.deepcopy(document)
    expected = [item['id'] for index, item in enumerate(document['elements']) if index < 35 and index != 5]
    arguments = {'ids': [item['id'] for item in document['elements'] if not item.get('isDeleted')],
                 'query': 'face STROKE', 'region': {'x': 0, 'y': 0, 'width': 100, 'height': 100}, 'limit': 7}
    found, states = [], set()
    while arguments is not None:
        page = inspect(document, arguments)
        assert page['matching_count'] == len(expected)
        assert page['returned_count'] == len(page['elements']) <= 7
        found.extend(item['id'] for item in page['elements'])
        states.add(page['scene_state'])
        arguments = page['next_page']
    assert found == expected and len(states) == 1
    assert document == original
    first = inspect(document, {'query': 'face', 'limit': 7})
    with pytest.raises(ValueError, match='inspection filters differ'):
        inspect(document, {**first['next_page'], 'query': 'Face stroke 1'})


def test_private_draft_changes_invalidate_pages_even_without_a_document_version_change():
    document = drawing()
    page = inspect(document, {'limit': 8})
    draft = copy.deepcopy(document)
    draft['elements'][20]['x'] += 5
    assert draft['version'] == document['version']
    with pytest.raises(ValueError, match='Scene changed'):
        inspect(draft, page['next_page'])
    refreshed = inspect(draft, {'limit': 8})
    assert refreshed['scene_state'] != page['scene_state']
    assert refreshed['offset'] == 0
    assert inspect(draft, refreshed['next_page'])['offset'] == 8


def test_unfiltered_pages_include_early_objects_outside_the_conversation_overview():
    document = drawing()
    document['elements'] += [create_native_element({'type': 'ellipse', 'name': f'Background {index}'}) for index in range(90)]
    expected = [item['id'] for item in document['elements'] if not item.get('isDeleted')]
    overview = scene_context(document)
    assert len(overview['elements']) == 12 and overview['overview_only'] and overview['has_more']
    assert document['selection'][0] in {item['id'] for item in overview['elements']}
    assert overview['type_counts'] == {'rectangle': 44, 'ellipse': 90}
    arguments, found = {'limit': 50}, []
    while arguments is not None:
        page = inspect(document, arguments)
        found.extend(item['id'] for item in page['elements'])
        arguments = page['next_page']
    assert found == expected
    assert inspect(document, {'query': 'not present'})['next_page'] is None


def test_context_retains_all_requested_object_ids_and_pagination_without_long_geometry(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'structured-inspection.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        try:
            session = store.create_session('笔画定位')
            document_id = session['document']['id']
            elements = [create_native_element({'type': 'freedraw', 'name': f'面部轮廓 {index}',
                'x': index * 20, 'y': 10, 'points': [[point, point % 11] for point in range(150)]}) for index in range(31)]
            document = store.save_snapshot(document_id, 0, elements, [])
            task = store.create_task(session['id'])
            models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            arguments = {'limit': 25, 'include_geometry': True}
            call = {'id': 'inspect-page', 'type': 'function', 'function': {
                'name': 'inspect_scene', 'arguments': json.dumps(arguments)}}
            output = await runtime.call_main_tool(models, task['id'], session['id'], document_id, call)
            context = runtime.memory.for_context(task['id'], 'main', f"main:{task['id']}", call['id'],
                'inspect_scene', call['function']['arguments'], output, f"{document_id}:{document['version']}")
            page = context['summary']['scene']
            assert [item['id'] for item in page['elements']] == [item['id'] for item in elements[:25]]
            assert page['matching_count'] == 31 and page['next_page']['offset'] == 25
            assert all(item['point_count'] == 150 for item in page['elements'])
            assert '"points"' not in json.dumps(context) and 'preview' not in context['summary']
            assert all(len(item['points']) == 150 for item in output['scene']['elements'])
            following = inspect(document, page['next_page'])
            assert [item['id'] for item in following['elements']] == [item['id'] for item in elements[25:]]
            assert store.get_document(document_id) == document
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_cached_unpaged_inspections_keep_compact_summaries_after_upgrade():
    document = drawing()
    output = {'ok': True, 'scene': scene_context(document, query='face')}
    summary = tool_summary('inspect_scene', output)
    assert len(json.dumps(summary)) < 7000
    assert 'scene_state' not in summary.get('scene', {})
    assert len(output['scene']['elements']) == 44

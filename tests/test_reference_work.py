import asyncio
import json

import pytest

from studio.reference_work import ReferenceWork
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore
from test_reference_layouts import layout, source


def workspace(tmp_path):
    store = StudioStore(tmp_path / 'reference-work.sqlite3')
    runtime = DrawingRuntime(store, ModelSettings(store))
    session = store.create_session()
    task = store.create_task(session['id'])
    reference_id = source(runtime.references, task)
    plan = layout(runtime.references, task, reference_id)
    message = json.dumps({'kind': 'reference_guided_repair', 'reference_layout_id': plan['reference_layout_id'],
                          'goal': 'Repair one connected contour and preserve other objects.'})
    return store, runtime, session, task, plan, message


def test_invalid_reference_inputs_do_not_spawn_or_enqueue_and_other_inputs_remain_supported(tmp_path):
    async def check():
        store, runtime, session, task, plan, message = workspace(tmp_path)
        try:
            for ordinary in ['Draw a pine tree', '{not json', '[]', '{"kind":"research"}']:
                assert ReferenceWork.load(runtime.references, task['id'], ordinary) is None
            config = ModelConfig(provider='ollama', model='unused', api_base='http://localhost:11434')
            value = json.loads(message)
            for invalid in [{**value, 'reference_layout_id': 'layout_' + '0' * 32},
                            {**value, 'reference_view_id': 'another-callers-view'}, {**value, 'goal': ' '}]:
                with pytest.raises((ValueError, KeyError)):
                    await runtime.children.spawn_agent(session['id'], task['id'], 'invalid-source',
                        {'message': json.dumps(invalid)}, config)
                assert store.agents.list_agents(session['id']) == []
            other = store.create_session()
            with pytest.raises(KeyError, match='another canvas'):
                ReferenceWork.load(runtime.references, store.create_task(other['id'])['id'], message)
            agent = store.agents.create_agent(session['id'], task['id'], 'original', 'Original drawing', 'Artist', [])
            before = store.agents.get_agent(session['id'], agent['agent_id'])
            with pytest.raises(ValueError):
                await runtime.children.send_input(session['id'], task['id'], 'invalid-queued-input', {
                    'agent_id': agent['agent_id'], 'message': json.dumps({**value, 'extra': True})}, config)
            assert store.agents.get_agent(session['id'], agent['agent_id']) == before
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_repair_schedules_own_source_and_layout_crop_without_forging_model_delivery(tmp_path):
    async def check():
        store, runtime, session, task, plan, message = workspace(tmp_path)
        try:
            repair = ReferenceWork.load(runtime.references, task['id'], message)
            repair.prepare_source('repair-run')
            messages, ids = await runtime.references.prepare_context(task['id'], 'repair-run')
            assert len(ids) == 2
            rows = store.connection.execute('SELECT id,region,delivered_at FROM reference_image_views WHERE caller_id=? '
                'ORDER BY created_at', ('repair-run',)).fetchall()
            assert all(row['delivered_at'] is None for row in rows)
            assert json.loads(rows[-1]['region']) == plan['source_frame'] and rows[-1]['id'] == ids[-1]
            assert sum(part.get('type') == 'image_url' for item in messages if isinstance(item['content'], list)
                       for part in item['content']) == 2
            with pytest.raises(ValueError, match='own model context'):
                await runtime.references.trace(task['id'], 'repair-run', {'name': 'not yet viewed',
                    'reference_id': plan['reference_id'], 'source_frame': plan['source_frame'], 'target': plan['target']})
            runtime.references.view(task['id'], 'repair-run', {'reference_id': plan['reference_id']})
            repair.prepare_source('repair-run')
            assert runtime.references.context(task['id'], 'repair-run')[1][-1] == ids[-1]
            other = source(runtime.references, task, color='red')
            requested = runtime.references.view(task['id'], 'repair-run', {'reference_id': other})
            repair.prepare_source('repair-run')
            assert runtime.references.context(task['id'], 'repair-run')[1][-1] == requested['reference_view_id']
            assert store.get_document(session['document']['id']) == session['document']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_preexisting_closeup_keeps_its_pixels_when_the_missing_full_source_is_scheduled(tmp_path):
    store, runtime, session, task, plan, message = workspace(tmp_path)
    try:
        repair = ReferenceWork.load(runtime.references, task['id'], message)
        detail = runtime.references.view(task['id'], 'detail-run', {'reference_id': plan['reference_id'],
            'region': {'x': 120, 'y': 100, 'width': 80, 'height': 80}})
        repair.prepare_source('detail-run')
        assert runtime.references.context(task['id'], 'detail-run')[1][-1] == detail['reference_view_id']
        rows = store.connection.execute('SELECT region,delivered_at FROM reference_image_views WHERE caller_id=?',
                                       ('detail-run',)).fetchall()
        assert len(rows) == 2 and any(row['region'] == 'null' for row in rows)
        assert all(row['delivered_at'] is None for row in rows)
    finally:
        store.close()


def test_full_source_layout_does_not_add_a_duplicate_image(tmp_path):
    store, runtime, session, task, plan, message = workspace(tmp_path)
    try:
        full = runtime.references.layouts.create(task['id'], 'main:' + task['id'], {
            'reference_id': plan['reference_id'], 'name': 'whole source',
            'target': {'x': 0, 'y': 0, 'width': 800, 'height': 600}})
        repair = ReferenceWork.load(runtime.references, task['id'], json.dumps({**json.loads(message),
            'reference_layout_id': full['reference_layout_id']}))
        repair.prepare_source('full-run')
        assert len(runtime.references.context(task['id'], 'full-run')[1]) == 1
        assert repair.context()['source_frame'] == {'x': 0, 'y': 0, 'width': 400, 'height': 300}
    finally:
        store.close()

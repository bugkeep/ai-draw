from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from studio.actions import create_native_element
from studio.manual_edits import ManualEdit, ManualEditConflict
from studio.store import StudioStore


def edit(before, after, fields, **extra):
    return {'id': (before or after)['id'], 'before': before, 'after': after, 'fields': fields, **extra}


def batch(changes, mutation='gesture-1', **extra):
    return ManualEdit(mutation_id=mutation, client_id='browser-1', changes=changes, **extra).model_dump()


def workspace(tmp_path):
    store = StudioStore(tmp_path / 'manual.sqlite3')
    session = store.create_session()
    elements = [create_native_element({'type': 'ellipse', 'name': '保留对象', 'x': 20, 'y': 40}),
                create_native_element({'type': 'rectangle', 'name': '手动对象', 'x': 220, 'y': 40})]
    document = store.save_snapshot(session['document']['id'], 0, elements, [])
    return store, document


def test_manual_color_merges_concurrent_geometry_and_preserves_current_other_objects_history_and_receipt(tmp_path):
    store, before = workspace(tmp_path)
    try:
        target = before['elements'][1]
        changed = {**target, 'strokeColor': '#ff0000'}
        request = batch([edit(target, changed, ['strokeColor'])])
        moved = store.commit_action(before['id'], {'kind': 'move_elements', 'args': {
            'ids': [target['id']], 'dx': 35, 'dy': 15}}, {})['document']
        saved = store.save_manual_edit(before['id'], request)['document']
        assert saved['elements'][1]['x'] == moved['elements'][1]['x']
        assert saved['elements'][1]['strokeColor'] == '#ff0000'
        assert saved['elements'][0] == moved['elements'][0]
        assert store.history_step(before['id'], 'undo')['elements'] == moved['elements']
        assert store.history_step(before['id'], 'redo')['elements'] == saved['elements']
        history = store.get_history(before['id'], 'undo')
        duplicate = store.save_manual_edit(before['id'], request)
        assert duplicate['already_committed'] and duplicate['document']['elements'] == saved['elements']
        assert store.get_history(before['id'], 'undo') == history
        newer = store.commit_action(before['id'], {'kind': 'move_elements', 'args': {
            'ids': [target['id']], 'dx': 20, 'dy': 0}}, {})['document']
        replay = store.save_manual_edit(before['id'], request)
        assert replay['document'] == newer and replay['already_committed']
        with pytest.raises(ValueError, match='identity cannot be reused'):
            store.save_manual_edit(before['id'], {**request, 'selection': [target['id']]})
    finally:
        store.close()


@pytest.mark.parametrize('operation', ['move', 'reshape', 'delete'])
def test_conflicting_geometry_or_deletion_is_atomic_and_keeps_other_local_edits_uncommitted(tmp_path, operation):
    store, before = workspace(tmp_path)
    try:
        target = before['elements'][1]
        if operation == 'reshape':
            target = create_native_element({'type': 'freedraw', 'name': '原始曲线', 'points': [[0, 0], [30, 15]]})
            before = store.save_snapshot(before['id'], before['version'], [before['elements'][0], target], [])
        if operation == 'delete':
            action = {'kind': 'delete_elements', 'args': {'ids': [target['id']]}}
        elif operation == 'reshape':
            action = {'kind': 'update_elements', 'args': {'ids': [target['id']], 'patch': {
                'points': [[0, 0], [30, 25]], 'height': 25}}}
        else:
            action = {'kind': 'move_elements', 'args': {'ids': [target['id']], 'dx': 50, 'dy': 0}}
        current = store.commit_action(before['id'], action, {})['document']
        local = {**target, 'x': target['x'] + 10} if operation != 'delete' else {**target, 'strokeColor': '#ff0000'}
        added = create_native_element({'type': 'diamond'})
        request = batch([edit(None, added, []), edit(target, local, ['x'] if operation != 'delete' else ['strokeColor'])])
        history = store.get_history(before['id'], 'undo')
        with pytest.raises(ManualEditConflict) as failure:
            store.save_manual_edit(before['id'], request)
        assert failure.value.conflicts[0]['id'] == target['id']
        assert store.get_document(before['id']) == current
        assert store.get_history(before['id'], 'undo') == history
        assert store.connection.execute('SELECT count(*) FROM manual_edit_receipts').fetchone()[0] == 0
    finally:
        store.close()


def test_two_actual_database_connections_merge_concurrent_browser_changes_without_lost_fields(tmp_path):
    first, before = workspace(tmp_path)
    second = StudioStore(first.database_path)
    target = before['elements'][1]
    barrier = Barrier(2)
    def save(store, field, value, client):
        request = batch([edit(target, {**target, field: value}, [field])])
        request['client_id'] = client
        barrier.wait()
        return store.save_manual_edit(before['id'], request)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(save, first, 'strokeColor', '#ff0000', 'window-a'),
                     pool.submit(save, second, 'backgroundColor', '#0000ff', 'window-b')]
            assert all(not task.result()['already_committed'] for task in tasks)
        final = first.get_document(before['id'])['elements'][1]
        assert final['strokeColor'] == '#ff0000' and final['backgroundColor'] == '#0000ff'
        assert first.connection.execute('SELECT count(*) FROM manual_edit_receipts').fetchone()[0] == 2
    finally:
        second.close()
        first.close()


def test_remote_additions_local_insertion_and_layer_reorder_keep_both_intents(tmp_path):
    store, before = workspace(tmp_path)
    try:
        first, second = before['elements']
        added = create_native_element({'type': 'diamond', 'name': '新手动对象'})
        remote = store.commit_action(before['id'], {'kind': 'create_element', 'args': {
            'type': 'ellipse', 'name': '新 Agent 对象'}}, {})['document']['elements'][-1]
        saved = store.save_manual_edit(before['id'], batch([edit(None, added, [], place_after=first['id'])],
            order_before=[first['id'], second['id']], order_after=[second['id'], first['id']]))['document']
        assert [item['id'] for item in saved['elements']] == [second['id'], first['id'], added['id'], remote['id']]
        assert next(item for item in saved['elements'] if item['id'] == remote['id']) == remote
    finally:
        store.close()


def test_event_failure_rolls_back_canvas_history_and_manual_receipt_together(tmp_path, monkeypatch):
    store, before = workspace(tmp_path)
    original = store._append_event
    target = before['elements'][1]
    request = batch([edit(target, {**target, 'x': 240}, ['x'])])
    def fail_event(*args):
        raise RuntimeError('intentional durable event failure')
    try:
        history = store.get_history(before['id'], 'undo')
        monkeypatch.setattr(store, '_append_event', fail_event)
        with pytest.raises(RuntimeError, match='intentional'):
            store.save_manual_edit(before['id'], request)
        assert store.get_document(before['id']) == before
        assert store.get_history(before['id'], 'undo') == history
        assert store.connection.execute('SELECT count(*) FROM manual_edit_receipts').fetchone()[0] == 0
        monkeypatch.setattr(store, '_append_event', original)
        assert store.save_manual_edit(before['id'], request)['document']['elements'][1]['x'] == 240
    finally:
        store.close()


def test_layer_conflict_and_incomplete_field_masks_cannot_claim_a_successful_save(tmp_path):
    store, before = workspace(tmp_path)
    try:
        target = before['elements'][1]
        with pytest.raises(ValueError, match='every intended'):
            store.save_manual_edit(before['id'], batch([edit(target, {**target, 'x': 250}, [])]))
        third = store.commit_action(before['id'], {'kind': 'create_element', 'args': {'type': 'ellipse'}}, {})['document']
        ids = [item['id'] for item in third['elements']]
        current = store.commit_action(before['id'], {'kind': 'reorder_elements', 'args': {
            'ids': [ids[1]], 'position': 'front'}}, {})['document']
        with pytest.raises(ManualEditConflict) as error:
            store.save_manual_edit(before['id'], batch([], order_before=ids, order_after=[ids[1], ids[0], ids[2]]))
        assert error.value.conflicts[0]['fields'] == ['order']
        assert store.get_document(before['id']) == current
        assert store.connection.execute('SELECT count(*) FROM manual_edit_receipts').fetchone()[0] == 0
    finally:
        store.close()


def test_real_http_manual_edit_conflict_returns_current_canvas_and_preserves_saved_receipt_on_reopen(tmp_path, monkeypatch):
    database = tmp_path / 'http-manual.sqlite3'
    monkeypatch.setenv('DRAW_AGENT_DB', str(database))
    with TestClient(create_app()) as client:
        store = client.app.state.studio_store
        session = store.create_session()
        before = store.save_snapshot(session['document']['id'], 0, [create_native_element({'type': 'ellipse'})], [])
        target = before['elements'][0]
        request = batch([edit(target, {**target, 'x': 30}, ['x'])])
        first = client.patch(f"/api/studio/documents/{before['id']}/scene", json=request)
        assert first.status_code == 200
        conflicting = batch([edit(target, {**target, 'x': 60}, ['x'])], mutation='another-gesture')
        response = client.patch(f"/api/studio/documents/{before['id']}/scene", json=conflicting)
        assert response.status_code == 409
        assert response.json()['detail']['document'] == first.json()['document']
        assert response.json()['detail']['conflicts'][0]['fields'] == ['x']
    with TestClient(create_app()) as client:
        replay = client.patch(f"/api/studio/documents/{before['id']}/scene", json=request)
        assert replay.json()['already_committed'] and replay.json()['document'] == first.json()['document']
        assert len(client.app.state.studio_store.get_history(before['id'], 'undo')) == 2

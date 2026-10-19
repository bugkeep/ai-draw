import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from server.app import create_app
from studio.api import document_events
from studio.store import StudioStore


def create_stroke(store, document_id, name='轮廓'):
    return store.commit_action(document_id, {'kind': 'create_element', 'args': {
        'type': 'freedraw', 'name': name, 'points': [[index, index % 17] for index in range(120)],
    }}, {})['document']


def move(store, document_id, element_id):
    return store.commit_action(document_id, {'kind': 'move_elements', 'args': {
        'ids': [element_id], 'dx': 1, 'dy': 2}}, {})['document']


def test_native_patch_contains_only_changes_and_preserves_layer_and_history_order(tmp_path):
    store = StudioStore(tmp_path / 'patches.sqlite3')
    try:
        document_id = store.create_session()['document']['id']
        for index in range(3):
            before = create_stroke(store, document_id, str(index))
        element_id = before['elements'][0]['id']
        moved = move(store, document_id, element_id)
        event = store.events_after(document_id, 3)[0]
        patch = event['payload']['patch']
        assert event['type'] == 'scene_patch' and 'document' not in event['payload']
        assert patch['base_version'] == before['version'] and patch['version'] == moved['version']
        assert patch['upserts'] == [moved['elements'][0]] and patch['removed_ids'] == []
        assert 'order' not in patch
        reordered = store.save_snapshot(document_id, moved['version'],
            list(reversed(moved['elements'][1:])), [])
        patch = store.events_after(document_id, event['seq'])[0]['payload']['patch']
        assert patch['removed_ids'] == [element_id] and patch['upserts'] == []
        assert patch['order'] == [element['id'] for element in reordered['elements']]
        undone = store.history_step(document_id, 'undo')
        assert undone['elements'] == moved['elements']
        patch = store.events_after(document_id, event['seq'] + 1)[0]['payload']['patch']
        assert patch['upserts'] == [moved['elements'][0]]
        assert patch['order'] == [element['id'] for element in moved['elements']]
        assert store.history_step(document_id, 'redo')['elements'] == reordered['elements']
    finally:
        store.close()


@pytest.mark.parametrize('operation', ['receipt', 'manual', 'undo', 'redo'])
def test_event_write_failure_rolls_back_canvas_history_and_receipt(tmp_path, operation):
    store = StudioStore(tmp_path / f'atomic-{operation}.sqlite3')
    try:
        session = store.create_session()
        document_id = session['document']['id']
        document = create_stroke(store, document_id)
        element_id = document['elements'][0]['id']
        if operation == 'redo':
            document = store.history_step(document_id, 'undo')
        task = store.create_task(session['id'])
        action = {'kind': 'move_elements', 'args': {'ids': [element_id], 'dx': 7, 'dy': 0}}
        result = store.agents.create_direct_result(session['id'], task['id'], 'move',
            [{'action': action, 'expected_elements': {}}], '移动已有轮廓')
        history = [store.get_history(document_id, direction) for direction in ('undo', 'redo')]
        events = store.events_after(document_id, 0)
        store.connection.executescript("CREATE TRIGGER reject_scene_event BEFORE INSERT ON events "
            "WHEN NEW.type='scene_patch' BEGIN SELECT RAISE(ABORT,'event-write-rejected'); END;")
        with pytest.raises(sqlite3.IntegrityError, match='event-write-rejected'):
            if operation == 'receipt':
                store.commit_action(document_id, action, {}, (result['id'], 0))
            elif operation == 'manual':
                store.save_snapshot(document_id, document['version'], [], [])
            else:
                store.history_step(document_id, operation)
        assert store.get_document(document_id) == document
        assert [store.get_history(document_id, direction) for direction in ('undo', 'redo')] == history
        assert store.events_after(document_id, 0) == events
        assert store.agents.get_result(session['id'], result['id'])['committed_count'] == 0
    finally:
        store.close()


def test_two_writers_commit_events_in_document_version_order(tmp_path):
    path = tmp_path / 'writers.sqlite3'
    first = StudioStore(path)
    second = StudioStore(path)
    try:
        document_id = first.create_session()['document']['id']
        document = create_stroke(first, document_id)
        element_id = document['elements'][0]['id']

        def draw(store):
            for _ in range(35):
                move(store, document_id, element_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(draw, store) for store in (first, second)]
            for future in futures:
                future.result()
        events = first.events_after(document_id, 0)
        assert [event['payload']['patch']['version'] for event in events] == list(range(1, 72))
        assert first.get_document(document_id)['elements'][0]['x'] == document['elements'][0]['x'] + 70
    finally:
        second.close()
        first.close()


def test_session_document_and_replay_cursor_share_one_sqlite_read_snapshot(tmp_path, monkeypatch):
    path = tmp_path / 'read-snapshot.sqlite3'
    reader = StudioStore(path)
    writer = StudioStore(path)
    try:
        session = reader.create_session()
        document_id = session['document']['id']
        document = create_stroke(writer, document_id)
        old_seq = reader.get_session(session['id'])['event_seq']
        get_document = reader.get_document

        def concurrent_write(document_id):
            current = get_document(document_id)
            move(writer, document_id, current['elements'][0]['id'])
            return current

        monkeypatch.setattr(reader, 'get_document', concurrent_write)
        snapshot = reader.get_session(session['id'])
        assert snapshot['document'] == document and snapshot['event_seq'] == old_seq
        assert writer.get_document(document_id)['version'] == document['version'] + 1
    finally:
        writer.close()
        reader.close()


def test_websocket_replays_exact_patches_and_retains_legacy_snapshot_clients(tmp_path, monkeypatch):
    monkeypatch.setenv('DRAW_AGENT_DB', str(tmp_path / 'websocket.sqlite3'))
    with TestClient(create_app()) as client:
        store = client.app.state.studio_store
        session = client.post('/api/studio/sessions', json={}).json()
        document_id = session['document']['id']
        document = create_stroke(store, document_id)
        for _ in range(502):
            document = move(store, document_id, document['elements'][0]['id'])
        with client.websocket_connect(f'/api/studio/documents/{document_id}/events?patches=true') as websocket:
            events = [websocket.receive_json() for _ in range(503)]
        assert [event['payload']['patch']['version'] for event in events] == list(range(1, 504))
        cursor = events[-1]['seq']
        with client.websocket_connect(f'/api/studio/documents/{document_id}/events?after={cursor}&patches=true') as websocket:
            websocket.send_text('client heartbeat')
            latest = move(store, document_id, document['elements'][0]['id'])
            patch = websocket.receive_json()
            assert patch['seq'] > cursor and patch['payload']['patch']['version'] == latest['version']
        with client.websocket_connect(f'/api/studio/documents/{document_id}/events?after={cursor}') as websocket:
            event = websocket.receive_json()
            assert event['type'] == 'scene' and event['payload']['document'] == latest
        with client.websocket_connect(f'/api/studio/documents/{document_id}/events?after={patch["seq"]}&patches=true') as websocket:
            assert client.delete(f'/api/studio/sessions/{session["id"]}').status_code == 200
            assert websocket.receive_json()['type'] == 'session_deleted'
        for _ in range(50):
            if not store.listeners:
                break
            time.sleep(0.01)
        assert not store.listeners


def test_simultaneous_inbound_text_and_commit_notification_cannot_drop_an_event(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'simultaneous.sqlite3')
        document_id = store.create_session()['document']['id']

        class Socket:
            app = SimpleNamespace(state=SimpleNamespace(studio_store=store))

            def __init__(self):
                self.incoming = asyncio.Queue()
                self.outgoing = asyncio.Queue()
                self.accepted = asyncio.Event()

            async def accept(self):
                self.accepted.set()

            async def receive_text(self):
                value = await self.incoming.get()
                if isinstance(value, Exception):
                    raise value
                return value

            async def send_json(self, event):
                self.outgoing.put_nowait(event)

        socket = Socket()
        worker = asyncio.create_task(document_events(socket, document_id, patches=True))
        try:
            await socket.accepted.wait()
            # Both inputs become ready before the event loop can resume the stream handler.
            socket.incoming.put_nowait('heartbeat')
            create_stroke(store, document_id)
            event = await asyncio.wait_for(socket.outgoing.get(), 2)
            assert event['type'] == 'scene_patch' and event['payload']['patch']['version'] == 1
            socket.incoming.put_nowait(WebSocketDisconnect())
            await asyncio.wait_for(worker, 2)
            assert not store.listeners
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            store.close()

    asyncio.run(check())

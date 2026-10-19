import json
import sqlite3

import pytest

from studio.store import StudioStore


def create_drawing(store):
    session = store.create_session('连续修改与撤销')
    created = store.commit_action(session['document']['id'], {'kind': 'create_element', 'args': {
        'type': 'freedraw', 'name': '山水 🌊 "轮廓"', 'strokeColor': '#495057',
        'points': [[index, index % 17] for index in range(150)],
    }}, {})['document']
    return session, created['elements'][0]['id']


def move(store, document_id, element_id, dx=1):
    return store.commit_action(document_id, {
        'kind': 'move_elements', 'args': {'ids': [element_id], 'dx': dx, 'dy': 0}}, {})['document']


def histories(store, document_id):
    row = store.connection.execute('SELECT undo_history,redo_history FROM documents WHERE id=?',
                                   (document_id,)).fetchone()
    return json.loads(row['undo_history']), json.loads(row['redo_history'])


def test_fifty_history_steps_survive_restart_with_native_geometry_and_order(tmp_path):
    path = tmp_path / 'history.sqlite3'
    store = StudioStore(path)
    session, element_id = create_drawing(store)
    document_id = session['document']['id']
    states = [store.get_document(document_id)]
    for _ in range(60):
        states.append(move(store, document_id, element_id))
    store.close()
    reopened = StudioStore(path)
    try:
        for expected in reversed(states[10:60]):
            actual = reopened.history_step(document_id, 'undo')
            assert actual['elements'] == expected['elements'] and actual['selection'] == expected['selection']
        assert reopened.history_step(document_id, 'undo') == reopened.get_document(document_id)
        assert len(histories(reopened, document_id)[1]) == 50
        for expected in states[11:61]:
            actual = reopened.history_step(document_id, 'redo')
            assert actual['elements'] == expected['elements'] and actual['selection'] == expected['selection']
        assert reopened.history_step(document_id, 'redo') == reopened.get_document(document_id)
        assert len(histories(reopened, document_id)[0]) == 50
    finally:
        reopened.close()


def test_selection_and_editor_metadata_preserve_redo_until_content_changes(tmp_path):
    store = StudioStore(tmp_path / 'metadata.sqlite3')
    try:
        session, element_id = create_drawing(store)
        document_id = session['document']['id']
        moved = move(store, document_id, element_id, 20)
        undone = store.history_step(document_id, 'undo')
        prior = histories(store, document_id)
        normalized = [{**undone['elements'][0], 'version': 111, 'versionNonce': 987,
                       'index': 'a0', 'boundElements': []}]
        saved = store.save_snapshot(document_id, undone['version'], normalized, undone['selection'])
        assert saved['version'] == undone['version'] and histories(store, document_id) == prior
        store.commit_action(document_id, {'kind': 'select_elements', 'args': {'ids': []}}, {})
        assert histories(store, document_id) == prior
        redone = store.history_step(document_id, 'redo')
        assert redone['elements'] == moved['elements'] and redone['selection'] == moved['selection']
        store.history_step(document_id, 'undo')
        branched = move(store, document_id, element_id, 5)
        assert histories(store, document_id)[1] == []
        assert store.history_step(document_id, 'redo') == branched
    finally:
        store.close()


def test_existing_json_history_keeps_latest_fifty_snapshots_after_edit_and_undo(tmp_path):
    store = StudioStore(tmp_path / 'existing.sqlite3')
    try:
        session, element_id = create_drawing(store)
        document_id = session['document']['id']
        states = [store.get_document(document_id)]
        for _ in range(60):
            states.append(move(store, document_id, element_id))
        existing = [{'elements': state['elements'], 'selection': state['selection']} for state in states[:-1]]
        with store.connection:
            store.connection.execute('UPDATE documents SET undo_history=? WHERE id=?',
                                     (json.dumps(existing, ensure_ascii=False), document_id))
        assert store.history_step(document_id, 'undo')['elements'] == states[-2]['elements']
        assert histories(store, document_id)[0] == existing[:-1][-50:]
        with store.connection:
            store.connection.execute('UPDATE documents SET undo_history=? WHERE id=?',
                                     (json.dumps(existing, ensure_ascii=False), document_id))
        before = store.get_document(document_id)
        move(store, document_id, element_id)
        assert histories(store, document_id)[0] == (existing + [{
            'elements': before['elements'], 'selection': before['selection']}])[-50:]
    finally:
        store.close()


def test_sqlite_receipt_failure_preserves_canvas_histories_and_commit_progress(tmp_path):
    store = StudioStore(tmp_path / 'transaction.sqlite3')
    try:
        session, element_id = create_drawing(store)
        document_id = session['document']['id']
        task = store.create_task(session['id'])
        action = {'kind': 'move_elements', 'args': {'ids': [element_id], 'dx': 7, 'dy': 0}}
        result = store.agents.create_direct_result(session['id'], task['id'], 'move',
            [{'action': action, 'expected_elements': {}}], '移动已有轮廓')
        before = store.connection.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()
        events = store.connection.execute('SELECT count(*) FROM events').fetchone()[0]
        store.connection.executescript("CREATE TRIGGER reject_receipt BEFORE UPDATE OF committed_count ON drawing_results "
            "BEGIN SELECT RAISE(ABORT,'receipt-write-rejected'); END;")
        with pytest.raises(sqlite3.IntegrityError, match='receipt-write-rejected'):
            store.commit_action(document_id, action, {}, (result['id'], 0))
        assert store.connection.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone() == before
        assert store.agents.get_result(session['id'], result['id'])['committed_count'] == 0
        assert store.connection.execute('SELECT count(*) FROM events').fetchone()[0] == events
        store.connection.execute('DROP TRIGGER reject_receipt')
        committed = store.commit_action(document_id, action, {}, (result['id'], 0))['document']
        assert store.agents.get_result(session['id'], result['id'])['committed_count'] == 1
        assert store.commit_action(document_id, action, {}, (result['id'], 0))['already_committed']
        assert store.get_document(document_id) == committed
        assert store.history_step(document_id, 'undo')['elements'] == json.loads(before['elements'])
        assert store.history_step(document_id, 'redo')['elements'] == committed['elements']
    finally:
        store.close()

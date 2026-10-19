import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

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
    return store.get_history(document_id, 'undo'), store.get_history(document_id, 'redo')


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


@pytest.mark.parametrize('operation', ['undo', 'edit'])
def test_existing_json_history_migrates_once_and_keeps_latest_fifty_after_edit_or_undo(tmp_path, operation):
    path = tmp_path / 'existing.sqlite3'
    store = StudioStore(path)
    session, element_id = create_drawing(store)
    document_id = session['document']['id']
    states = [store.get_document(document_id)]
    for _ in range(60):
        states.append(move(store, document_id, element_id))
    existing = [{'elements': state['elements'], 'selection': state['selection']} for state in states[:-1]]
    redo = existing[20:23]
    with store.connection:
        store.connection.execute('DELETE FROM document_history WHERE document_id=?', (document_id,))
        store.connection.execute('UPDATE documents SET undo_history=?,redo_history=? WHERE id=?',
                                 (json.dumps(existing, ensure_ascii=False), json.dumps(redo), document_id))
    store.close()
    store = StudioStore(path)
    assert histories(store, document_id) == (existing, redo)
    assert tuple(store.connection.execute('SELECT undo_history,redo_history FROM documents WHERE id=?',
                                         (document_id,)).fetchone()) == ('[]', '[]')
    store.close()
    store = StudioStore(path)
    try:
        assert histories(store, document_id) == (existing, redo)
        if operation == 'undo':
            assert store.history_step(document_id, 'undo')['elements'] == states[-2]['elements']
            assert histories(store, document_id)[0] == existing[:-1][-50:]
        else:
            before = store.get_document(document_id)
            move(store, document_id, element_id)
            assert histories(store, document_id)[0] == (existing + [{
                'elements': before['elements'], 'selection': before['selection']}])[-50:]
            assert histories(store, document_id)[1] == []
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
        before_history = histories(store, document_id)
        events = store.connection.execute('SELECT count(*) FROM events').fetchone()[0]
        store.connection.executescript("CREATE TRIGGER reject_receipt BEFORE UPDATE OF committed_count ON drawing_results "
            "BEGIN SELECT RAISE(ABORT,'receipt-write-rejected'); END;")
        with pytest.raises(sqlite3.IntegrityError, match='receipt-write-rejected'):
            store.commit_action(document_id, action, {}, (result['id'], 0))
        assert store.connection.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone() == before
        assert histories(store, document_id) == before_history
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


def test_history_migration_failure_preserves_both_legacy_directions(tmp_path):
    path = tmp_path / 'migration-failure.sqlite3'
    store = StudioStore(path)
    session, element_id = create_drawing(store)
    document_id = session['document']['id']
    move(store, document_id, element_id)
    undo = json.dumps(store.get_history(document_id, 'undo'), ensure_ascii=False)
    redo = json.dumps([{'elements': store.get_document(document_id)['elements'], 'selection': [element_id]}])
    with store.connection:
        store.connection.execute('DELETE FROM document_history')
        store.connection.execute('UPDATE documents SET undo_history=?,redo_history=? WHERE id=?',
                                 (undo, redo, document_id))
    store.connection.executescript("CREATE TRIGGER reject_history BEFORE INSERT ON document_history "
        "WHEN NEW.direction='redo' BEGIN SELECT RAISE(ABORT,'history-write-rejected'); END;")
    store.close()
    with pytest.raises(sqlite3.IntegrityError, match='history-write-rejected'):
        StudioStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT count(*) FROM document_history').fetchone()[0] == 0
        assert connection.execute('SELECT undo_history,redo_history FROM documents WHERE id=?',
                                  (document_id,)).fetchone() == (undo, redo)
        connection.execute('DROP TRIGGER reject_history')
    migrated = StudioStore(path)
    try:
        assert histories(migrated, document_id) == (json.loads(undo), json.loads(redo))
        migrated.delete_session(session['id'])
        assert migrated.connection.execute('SELECT count(*) FROM document_history').fetchone()[0] == 0
    finally:
        migrated.close()


def test_failed_undo_preserves_history_rows_and_other_documents(tmp_path):
    store = StudioStore(tmp_path / 'undo-transaction.sqlite3')
    try:
        session, element_id = create_drawing(store)
        other, other_id = create_drawing(store)
        document_id = session['document']['id']
        move(store, document_id, element_id)
        move(store, other['document']['id'], other_id, 20)
        document = store.get_document(document_id)
        snapshots = list(store.connection.execute('SELECT * FROM document_history ORDER BY id'))
        store.connection.executescript("CREATE TRIGGER reject_scene BEFORE UPDATE OF elements ON documents "
            "BEGIN SELECT RAISE(ABORT,'scene-write-rejected'); END;")
        with pytest.raises(sqlite3.IntegrityError, match='scene-write-rejected'):
            store.history_step(document_id, 'undo')
        assert store.get_document(document_id) == document
        assert list(store.connection.execute('SELECT * FROM document_history ORDER BY id')) == snapshots
        store.connection.execute('DROP TRIGGER reject_scene')
        store.history_step(document_id, 'undo')
        assert [row for row in store.connection.execute('SELECT * FROM document_history ORDER BY id')
                if row['document_id'] == other['document']['id']] == [
                    row for row in snapshots if row['document_id'] == other['document']['id']]
    finally:
        store.close()


def test_edit_keeps_prior_history_rows_and_geometry_unchanged(tmp_path):
    store = StudioStore(tmp_path / 'retained-history.sqlite3')
    try:
        session, element_id = create_drawing(store)
        document_id = session['document']['id']
        for _ in range(5):
            move(store, document_id, element_id)
        snapshots = list(store.connection.execute('SELECT * FROM document_history ORDER BY id'))
        move(store, document_id, element_id)
        actual = list(store.connection.execute('SELECT * FROM document_history ORDER BY id'))
        assert actual[:-1] == snapshots
        assert tuple(store.connection.execute('SELECT undo_history,redo_history FROM documents WHERE id=?',
                                             (document_id,)).fetchone()) == ('[]', '[]')
    finally:
        store.close()


def test_two_store_connections_serialize_canvas_and_history_updates(tmp_path):
    path = tmp_path / 'concurrent-history.sqlite3'
    first, second = StudioStore(path), StudioStore(path)
    try:
        session, element_id = create_drawing(first)
        document_id = session['document']['id']
        initial = first.get_document(document_id)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda index: move(first if index % 2 else second,
                                                          document_id, element_id), range(20)))
        assert sorted(item['elements'][0]['x'] for item in results) == list(range(1, 21))
        assert first.get_document(document_id)['version'] == initial['version'] + 20
        assert len(first.get_history(document_id, 'undo')) == 21
        for _ in range(20):
            first.history_step(document_id, 'undo')
        assert first.get_document(document_id)['elements'] == initial['elements']
        assert len(first.get_history(document_id, 'redo')) == 20
    finally:
        first.close()
        second.close()


def test_migration_rejects_mixed_storage_without_changing_history(tmp_path):
    path = tmp_path / 'mixed-history.sqlite3'
    store = StudioStore(path)
    session, _ = create_drawing(store)
    document_id = session['document']['id']
    snapshots = [tuple(row) for row in store.connection.execute('SELECT * FROM document_history ORDER BY id')]
    legacy = json.dumps(store.get_history(document_id, 'undo'))
    with store.connection:
        store.connection.execute('UPDATE documents SET undo_history=? WHERE id=?', (legacy, document_id))
    store.close()
    with pytest.raises(ValueError, match='two storage formats'):
        StudioStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT undo_history FROM documents WHERE id=?', (document_id,)).fetchone()[0] == legacy
        assert connection.execute('SELECT * FROM document_history ORDER BY id').fetchall() == snapshots
